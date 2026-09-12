import hashlib
import logging
import mimetypes
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import admin_user, current_user
from app.catalog import get_vehicle
from app.config import Settings
from app.db import get_session, session_factory
from app.models import ImportJob, Manual, ManualAsset, ManualVersion, new_id, utcnow

router = APIRouter()
log = logging.getLogger("rag")


def dispatch_job(job_id):
    # A committed queued row is the outbox. Beat reconciles if broker delivery fails.
    try:
        from app.tasks import import_manual
        import_manual.apply_async(args=[job_id], retry=False)
    except Exception as exc:
        log.warning("job_dispatch_failed", extra={"job_id": job_id, "error_type": type(exc).__name__})


def job_view(job):
    return {"id": job.id, "status": job.status, "error": job.error, "attempts": job.attempts}


def upload_view(version, job):
    return {"manual_id": version.manual_id, "version_id": version.id, "job_id": job.id, "status": job.status}


def active_version_ids(vehicle_id: str) -> set[str]:
    with session_factory()() as session:
        return set(session.scalars(select(Manual.active_version_id).where(
            Manual.vehicle_id == vehicle_id, Manual.enabled.is_(True),
            Manual.active_version_id.is_not(None))))


def _store_upload(manual: Manual, file: UploadFile, session: Session):
    root = Path(Settings().storage_path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    temporary = None
    final = None
    try:
        with NamedTemporaryFile(dir=root, suffix=".upload", delete=False) as stream:
            temporary = Path(stream.name)
            digest = hashlib.sha256()
            size = 0
            while data := file.file.read(1024 * 1024):
                if size == 0 and not data.startswith(b"%PDF-"):
                    raise HTTPException(422, "请上传有效的 PDF 文件。")
                size += len(data)
                if size > Settings().upload_max_bytes:
                    raise HTTPException(413, "PDF 超过上传大小限制。")
                digest.update(data)
                stream.write(data)
        if size == 0:
            raise HTTPException(422, "文件为空。")
        # Serialize versions of the same manual; PostgreSQL is the deployment DB.
        session.execute(select(Manual).where(Manual.id == manual.id).with_for_update()).scalar_one()
        previous = session.scalar(select(ManualVersion).where(
            ManualVersion.manual_id == manual.id, ManualVersion.sha256 == digest.hexdigest()))
        if previous:
            job = session.scalar(select(ImportJob).where(ImportJob.version_id == previous.id))
            session.commit()
            return upload_view(previous, job)
        version_id = new_id()
        final = root / (version_id + ".pdf")
        os.replace(temporary, final)
        temporary = None
        version = ManualVersion(id=version_id, manual_id=manual.id, sha256=digest.hexdigest(),
                                file_path=final.name)
        session.add(version)
        session.flush()
        job = ImportJob(version_id=version.id)
        session.add(job)
        session.commit()
        final = None  # committed file is now owned by the version
        dispatch_job(job.id)
        return upload_view(version, job)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
        if final:
            final.unlink(missing_ok=True)
        file.file.close()


@router.get("/admin/manuals", dependencies=[Depends(admin_user)])
def list_manuals(session: Session = Depends(get_session)):
    result = []
    for manual in session.scalars(select(Manual).order_by(Manual.title)):
        versions = []
        for version in session.scalars(select(ManualVersion).where(
                ManualVersion.manual_id == manual.id).order_by(ManualVersion.created_at.desc())):
            job = session.scalar(select(ImportJob).where(ImportJob.version_id == version.id))
            versions.append({"id": version.id, "sha256": version.sha256, "page_count": version.page_count,
                             "chunk_count": version.chunk_count, "created_at": version.created_at.isoformat() + "Z",
                             "job": job_view(job)})
        result.append({"id": manual.id, "vehicle_id": manual.vehicle_id, "title": manual.title,
                       "source": manual.source, "enabled": manual.enabled,
                       "active_version_id": manual.active_version_id, "versions": versions})
    return result


@router.post("/admin/manuals", status_code=202, dependencies=[Depends(admin_user)])
def upload_manual(vehicle_id: str = Form(...), title: str = Form(...), source: str = Form(...),
                  file: UploadFile = File(...), session: Session = Depends(get_session)):
    if get_vehicle(vehicle_id) is None:
        raise HTTPException(422, "不支持的车型。")
    if not title.strip() or len(title) > 200 or not source.strip() or len(source) > 4000:
        raise HTTPException(422, "请填写标题（最多 200 字）和来源（最多 4000 字）。")
    manual = session.scalar(select(Manual).where(Manual.vehicle_id == vehicle_id, Manual.title == title.strip()))
    if manual is None:
        manual = Manual(vehicle_id=vehicle_id, title=title.strip(), source=source.strip())
        session.add(manual)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            manual = session.scalar(select(Manual).where(
                Manual.vehicle_id == vehicle_id, Manual.title == title.strip()))
    return _store_upload(manual, file, session)


@router.post("/admin/manuals/{manual_id}/versions", status_code=202, dependencies=[Depends(admin_user)])
def upload_version(manual_id: str, file: UploadFile = File(...), session: Session = Depends(get_session)):
    manual = session.get(Manual, manual_id)
    if manual is None:
        raise HTTPException(404, "手册不存在。")
    return _store_upload(manual, file, session)


class ManualUpdate(BaseModel):
    enabled: bool


@router.patch("/admin/manuals/{manual_id}", dependencies=[Depends(admin_user)])
def update_manual(manual_id: str, body: ManualUpdate, session: Session = Depends(get_session)):
    manual = session.get(Manual, manual_id)
    if manual is None:
        raise HTTPException(404, "手册不存在。")
    manual.enabled = body.enabled
    session.commit()
    return {"id": manual.id, "enabled": manual.enabled}


@router.get("/jobs/{job_id}", dependencies=[Depends(admin_user)])
def get_job(job_id: str, session: Session = Depends(get_session)):
    job = session.get(ImportJob, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在。")
    return job_view(job)


@router.post("/jobs/{job_id}/retry", dependencies=[Depends(admin_user)])
def retry_job(job_id: str, session: Session = Depends(get_session)):
    job = session.scalar(select(ImportJob).where(ImportJob.id == job_id).with_for_update())
    if job is None:
        raise HTTPException(404, "任务不存在。")
    if job.status != "failed":
        raise HTTPException(409, "只有失败任务可以重试。")
    job.status, job.attempts, job.error, job.lease_token = "queued", 0, None, None
    job.updated_at = utcnow()
    session.commit()
    dispatch_job(job.id)
    return job_view(job)


@router.get("/manuals/{manual_id}/versions/{version_id}/file", dependencies=[Depends(current_user)])
def original_pdf(manual_id: str, version_id: str, session: Session = Depends(get_session)):
    version = session.get(ManualVersion, version_id)
    if version is None or version.manual_id != manual_id:
        raise HTTPException(404, "手册版本不存在。")
    root = Path(Settings().storage_path).resolve()
    path = (root / version.file_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "原文文件不可用。")
    return FileResponse(path, media_type="application/pdf", filename=f"{version.id}.pdf",
                        content_disposition_type="inline", headers={"Cache-Control": "private, no-store"})


@router.get("/manuals/{manual_id}/versions/{version_id}/assets/{asset_id}/file",
            dependencies=[Depends(current_user)])
def asset_file(manual_id: str, version_id: str, asset_id: str, session: Session = Depends(get_session)):
    version = session.get(ManualVersion, version_id)
    asset = session.get(ManualAsset, asset_id)
    if version is None or version.manual_id != manual_id or asset is None or asset.version_id != version_id:
        raise HTTPException(404, "图片不存在。")
    root = Path(Settings().storage_path).resolve()
    path = (root / asset.file_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "图片不可用。")
    media_type = mimetypes.guess_type(path.name)[0]
    if media_type is None or not media_type.startswith("image/"):
        raise HTTPException(404, "图片不可用。")
    return FileResponse(path, media_type=media_type, filename=path.name,
                        content_disposition_type="inline", headers={"Cache-Control": "private, no-store"})
