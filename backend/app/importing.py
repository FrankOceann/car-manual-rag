import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update

from app.catalog import get_vehicle
from app.config import Settings
from app.db import session_factory
from app.models import ImportJob, Manual, ManualVersion, new_id, utcnow
from app.rag.chunking import extract_chunks, ManualChunk
from app.rag.store import ManualStore

log = logging.getLogger("rag")
MAX_ATTEMPTS = 4  # initial attempt plus three retries
LEASE_SECONDS = 1200


class LeaseLost(Exception):
    pass


class NoTextPDF(ValueError):
    pass


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
            ImportJob.status.in_(["parsing", "embedding"])).values(status=status, updated_at=utcnow()))
        session.commit()
        if result.rowcount != 1:
            raise LeaseLost()


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
        path = Path(Settings().storage_path).resolve() / version.file_path
    log.info("import_started", extra={"job_id": job_id})
    try:
        from pypdf import PdfReader
        page_count = len(PdfReader(path).pages)
        raw = extract_chunks(path, get_vehicle(manual.vehicle_id), manual.title)
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
            ImportJob.status.in_(["parsing", "embedding"]),
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
