import hashlib
import logging
from datetime import timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, update

from app.catalog import get_vehicle
from app.config import Settings
from app.db import session_factory
from app.models import ImportJob, Manual, ManualAsset, ManualVersion, new_id, utcnow
from app.rag.chunking import chunk_evidence_text, extract_chunks, ManualChunk
from app.rag.store import ManualStore
from app.rag.visual import RapidOCRClient, VisualPage, extract_visual_pages

log = logging.getLogger("rag")
MAX_ATTEMPTS = 4  # initial attempt plus three retries
LEASE_SECONDS = 1200


class LeaseLost(Exception):
    pass


class NoTextPDF(ValueError):
    pass


class _PageOCRClient:
    def __init__(self, client, job_id):
        self.client, self.job_id = client, job_id

    def recognize(self, image: bytes) -> str:
        try:
            return self.client.recognize(image)
        except Exception as exc:
            log.warning("page_ocr_failed", extra={"job_id": self.job_id, "error_type": type(exc).__name__})
            return ""


def transient_failure(error: Exception) -> bool:
    import httpx
    from sqlalchemy.exc import OperationalError
    from chromadb.errors import InternalError, RateLimitError
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, (ConnectionError, TimeoutError, httpx.TransportError,
                              OperationalError, InternalError, RateLimitError)):
            return True
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code == 429 or error.response.status_code >= 500
        error = error.__cause__ or error.__context__
    return False


def _progress(job_id, token, status):
    with session_factory()() as session:
        result = session.execute(update(ImportJob).where(
            ImportJob.id == job_id, ImportJob.lease_token == token,
            ImportJob.status.in_(["parsing", "ocr", "embedding"])).values(status=status, updated_at=utcnow()))
        session.commit()
        if result.rowcount != 1:
            raise LeaseLost()


def persist_visual_assets(session, version, visual_pages: list[VisualPage], root: Path) -> list[ManualAsset]:
    root = root.resolve()
    # Serialize asset reuse within the version, including recovered worker attempts.
    session.execute(select(ManualVersion).where(ManualVersion.id == version.id).with_for_update()).scalar_one()
    assets = []
    for page in visual_pages:
        for extracted in page.assets:
            digest = hashlib.sha256(extracted.data).hexdigest()
            asset = session.scalar(select(ManualAsset).where(
                ManualAsset.version_id == version.id, ManualAsset.page_number == extracted.page_number,
                ManualAsset.asset_index == extracted.asset_index, ManualAsset.sha256 == digest))
            if not extracted.suffix.isalnum():
                raise ValueError("Invalid image suffix")
            relative = Path("assets") / version.id / (
                f"p{extracted.page_number}-a{extracted.asset_index}-{digest}.{extracted.suffix}")
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Asset path escapes storage root")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(extracted.data)
            if asset is None:
                asset = ManualAsset(
                    id=str(uuid5(NAMESPACE_URL, f"{version.id}/{extracted.page_number}/{extracted.asset_index}/{digest}")),
                    version_id=version.id, page_number=extracted.page_number, asset_index=extracted.asset_index,
                    sha256=digest, file_path=relative.as_posix(), width=extracted.width, height=extracted.height,
                    processing_status="pending")
                session.add(asset)
                session.flush()
            assets.append(asset)
    return assets


def process_job(job_id: str):
    token = new_id()
    with session_factory()() as session:
        claim = session.execute(update(ImportJob).where(
            ImportJob.id == job_id, ImportJob.status == "queued", ImportJob.attempts < MAX_ATTEMPTS
        ).values(status="parsing", attempts=ImportJob.attempts + 1,
                 lease_token=token, updated_at=utcnow(), error=None))
        session.commit()
        if claim.rowcount != 1:
            return
        job = session.get(ImportJob, job_id)
        version = session.get(ManualVersion, job.version_id)
        manual = session.get(Manual, version.manual_id)
        settings = Settings()
        root = Path(settings.storage_path).resolve()
        path = root / version.file_path
    log.info("import_started", extra={"job_id": job_id})
    try:
        from pypdf import PdfReader
        page_count = len(PdfReader(path).pages)
        vehicle = get_vehicle(manual.vehicle_id)
        raw = extract_chunks(path, vehicle, manual.title)
        if settings.ocr_enabled:
            _progress(job_id, token, "ocr")
            ocr = RapidOCRClient()
            try:
                visual_pages = extract_visual_pages(path, ocr=_PageOCRClient(ocr, job_id), dpi=settings.ocr_render_dpi,
                                                    run_page_ocr=lambda text: not text.strip())
            except Exception as exc:
                # Optional OCR/extraction must not discard usable PDF text.
                log.warning("visual_extraction_failed", extra={"job_id": job_id, "error_type": type(exc).__name__})
                visual_pages = []
            with session_factory()() as session:
                assets = persist_visual_assets(session, version, visual_pages, root)
                session.commit()
                extracted = [asset for page in visual_pages for asset in page.assets]
                for asset, source in zip(assets, extracted):
                    _progress(job_id, token, "ocr")
                    try:
                        asset.ocr_text = ocr.recognize(source.data)
                        asset.processing_status, asset.error = "succeeded", None
                    except Exception:
                        asset.processing_status, asset.error = "failed", "图片 OCR 处理失败。"
                    session.commit()
                    if asset.ocr_text.strip():
                        raw.extend(chunk_evidence_text(
                            asset.ocr_text, page_number=asset.page_number, vehicle=vehicle,
                            manual_title=manual.title, chapter_title=f"第 {asset.page_number} 页",
                            evidence_type="image_ocr", asset_id=asset.id))
            for page in visual_pages:
                if page.page_ocr_text.strip():
                    raw.extend(chunk_evidence_text(
                        page.page_ocr_text, page_number=page.page_number, vehicle=vehicle,
                        manual_title=manual.title, chapter_title=f"第 {page.page_number} 页",
                        evidence_type="page_ocr"))
        if not raw:
            raise NoTextPDF("PDF 无可提取文本，请提供文字版 PDF。")
        chunks = [ManualChunk(
            id=f"{version.id}-{chunk.id}",
            text=chunk.text, metadata=chunk.metadata | {"manual_id": manual.id, "version_id": version.id}
        ) for chunk in raw]
        _progress(job_id, token, "embedding")
        store = ManualStore()
        for offset in range(0, len(chunks), 64):
            _progress(job_id, token, "embedding")
            store.upsert(chunks[offset:offset + 64])
        with session_factory()() as session:
            job = session.scalar(select(ImportJob).where(ImportJob.id == job_id).with_for_update())
            if job.lease_token != token or job.status != "embedding":
                raise LeaseLost()
            # Finish reconciliation under the job lock before making this version visible.
            # A failed cleanup follows the same retry path as a failed vector write.
            store.reconcile_version(version.id, {chunk.id for chunk in chunks})
            version = session.get(ManualVersion, job.version_id)
            manual = session.scalar(select(Manual).where(Manual.id == version.manual_id).with_for_update())
            active = session.get(ManualVersion, manual.active_version_id) if manual.active_version_id else None
            # A slower older job must never replace a newer successful version.
            if active is None or (version.created_at, version.id) > (active.created_at, active.id):
                manual.active_version_id = version.id
            version.page_count, version.chunk_count = page_count, len(chunks)
            job.status, job.lease_token, job.error, job.updated_at = "succeeded", None, None, utcnow()
            session.commit()
        log.info("import_succeeded", extra={"job_id": job_id})
    except LeaseLost:
        return
    except Exception as exc:
        # Retry only transient infrastructure failures; deterministic bad PDFs fail immediately.
        transient = transient_failure(exc)
        error = ("依赖连接失败，可重试。" if transient else
                 "PDF 无可提取文本。" if isinstance(exc, NoTextPDF) else "处理失败，请检查 PDF 和本地模型配置。")
        with session_factory()() as session:
            job = session.scalar(select(ImportJob).where(
                ImportJob.id == job_id, ImportJob.lease_token == token).with_for_update())
            if job is not None:
                job.status = "queued" if transient and job.attempts < MAX_ATTEMPTS else "failed"
                job.error, job.lease_token, job.updated_at = error, None, utcnow()
                session.commit()
        log.warning("import_failed", extra={"job_id": job_id, "error_type": type(exc).__name__})


def recover_jobs():
    now = utcnow()
    with session_factory()() as session:
        stale = list(session.scalars(select(ImportJob).where(
            ImportJob.status.in_(["parsing", "ocr", "embedding"]),
            ImportJob.updated_at < now - timedelta(seconds=LEASE_SECONDS)).with_for_update(skip_locked=True)))
        for job in stale:
            job.status = "queued" if job.attempts < MAX_ATTEMPTS else "failed"
            job.lease_token, job.error, job.updated_at = None, "任务中断，已恢复状态。", now
        session.commit()
        queued = list(session.scalars(select(ImportJob.id).where(
            ImportJob.status == "queued", ImportJob.updated_at < now - timedelta(seconds=30))))
    from app.management import dispatch_job
    for job_id in queued:
        dispatch_job(job_id)
    return len(queued)
