from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def new_id():
    return str(uuid4())


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(10), default="reader")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Manual(Base):
    __tablename__ = "manuals"
    __table_args__ = (UniqueConstraint("vehicle_id", "title"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    vehicle_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    active_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ManualVersion(Base):
    __tablename__ = "manual_versions"
    __table_args__ = (UniqueConstraint("manual_id", "sha256"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    manual_id: Mapped[str] = mapped_column(ForeignKey("manuals.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    file_path: Mapped[str] = mapped_column(Text)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    processing_config: Mapped[str] = mapped_column(Text, default=(
        '{"chunk_size":900,"overlap":120,"embedding_model":'
        '"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"}'))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ManualAsset(Base):
    __tablename__ = "manual_assets"
    __table_args__ = (UniqueConstraint("version_id", "page_number", "asset_index", "sha256"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("manual_versions.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    file_path: Mapped[str] = mapped_column(Text)
    page_number: Mapped[int] = mapped_column(Integer)
    asset_index: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    visual_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportJob(Base):
    __tablename__ = "import_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("manual_versions.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
