"""Opt-in: real PostgreSQL, Redis worker and Chroma; self-authored PDF and fake embeddings."""
import os
import time
from io import BytesIO
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_SERVICE_TESTS") != "1",
                                reason="requires isolated PostgreSQL, Redis and Chroma")


def test_queue_import_and_retrieve_across_real_services(monkeypatch, tmp_path):
    import chromadb
    from celery.contrib.testing.worker import start_worker
    from fastapi.testclient import TestClient
    from sqlalchemy import select, delete
    from alembic.config import Config
    from alembic import command
    from app.config import Settings
    from app.db import get_engine, session_factory
    from app.auth import hash_password
    from app.models import User, Manual, ManualVersion, ImportJob
    from app.main import app
    from app.rag.store import ManualStore
    from app.tasks import celery_app
    from tests.test_enterprise import pdf_bytes

    assert Settings().database_url.startswith("postgresql"), "integration requires PostgreSQL"
    tag = uuid4().hex
    monkeypatch.setenv("JWT_SECRET", "integration-only-" + tag)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "manuals"))
    get_engine.cache_clear()
    session_factory.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    chroma = chromadb.HttpClient(host=Settings().chroma_host, port=Settings().chroma_port)
    collection_name = "integration-" + tag
    collection = chroma.create_collection(collection_name, metadata={"hnsw:space": "cosine"})
    monkeypatch.setattr(ManualStore, "_create_collection", staticmethod(lambda _: collection))
    class Embeddings:
        def encode(self, texts):
            # Deterministic unit vectors exercise serialization/storage, not semantic quality.
            return [[1.0, 0.0, 0.0] for _ in texts]
    monkeypatch.setattr(ManualStore, "_create_embedding_model", staticmethod(Embeddings))
    username = "integration-" + tag[:12]
    with session_factory()() as session:
        user = User(username=username, password_hash=hash_password("integration-password"), role="admin")
        session.add(user)
        session.commit()
        user_id = user.id
    celery_app.conf.update(broker_url=Settings().redis_url, task_default_queue="integration-" + tag)
    manual_ids = []
    try:
        with start_worker(celery_app, pool="solo", perform_ping_check=False, shutdown_timeout=15):
            with TestClient(app) as client:
                login = client.post("/auth/login", json={"username": username, "password": "integration-password"})
                assert login.status_code == 200
                headers = {"Authorization": "Bearer " + login.json()["access_token"]}
                result = client.post("/admin/manuals", headers=headers, data={
                    "vehicle_id": "toyota-corolla", "title": username, "source": "Self-authored test"},
                    files={"file": ("demo.pdf", pdf_bytes(), "application/pdf")})
                assert result.status_code == 202
                job = result.json()
                manual_ids.append(job["manual_id"])
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    status = client.get("/jobs/" + job["job_id"], headers=headers).json()
                    if status["status"] in ("succeeded", "failed"):
                        break
                    time.sleep(0.2)
                assert status["status"] == "succeeded", status
                store = ManualStore(version_ids={job["version_id"]})
                found = store.query("toyota-corolla", "tire pressure", limit=1)
                assert len(found) == 1
                assert found[0].metadata["page_number"] == 1
                assert found[0].metadata["manual_id"] == job["manual_id"]
                assert not store.query("byd-seal", "tire", limit=1)
                original = client.get(
                    f'/manuals/{job["manual_id"]}/versions/{job["version_id"]}/file', headers=headers)
                assert original.content == pdf_bytes()
                assert client.patch("/admin/manuals/" + job["manual_id"],
                                    headers=headers, json={"enabled": False}).status_code == 200
                response = client.post("/chat", headers=headers,
                                       json={"vehicle_id": "toyota-corolla", "question": "test"})
                assert response.status_code == 200
                assert response.json()["grounded"] is False
    finally:
        chroma.delete_collection(collection_name)
        with session_factory()() as session:
            versions = select(ManualVersion.id).where(ManualVersion.manual_id.in_(manual_ids))
            session.execute(delete(ImportJob).where(ImportJob.version_id.in_(versions)))
            session.execute(delete(ManualVersion).where(ManualVersion.manual_id.in_(manual_ids)))
            session.execute(delete(Manual).where(Manual.id.in_(manual_ids)))
            session.execute(delete(User).where(User.id == user_id))
            session.commit()
        get_engine().dispose()
        get_engine.cache_clear()
        session_factory.cache_clear()
