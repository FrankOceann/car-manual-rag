from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.main import app


class ImportStoreStub:
    # Upsert-focused tests have no stale collection; reconciliation is exercised below
    # with the real ManualStore and an embedded Chroma collection.
    def reconcile_version(self, version_id, chunk_ids):
        pass


@pytest.fixture
def session(tmp_path):
    from app.db import Base

    engine = create_engine("sqlite:///" + str(tmp_path / "assets.db"))
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as database_session:
        yield database_session
    Base.metadata.drop_all(engine)
    engine.dispose()


def make_version(session):
    from app.models import Manual, ManualVersion

    manual = Manual(vehicle_id="test-car", title="Test manual", source="test")
    session.add(manual)
    session.flush()
    version = ManualVersion(manual_id=manual.id, sha256="b" * 64, file_path="manuals/test.pdf")
    session.add(version)
    session.commit()
    return version


def test_manual_asset_is_unique_within_a_version(session):
    from app.models import ManualAsset

    version = make_version(session)
    session.add_all([
        ManualAsset(version_id=version.id, page_number=2, asset_index=0, sha256="a" * 64,
                    file_path="assets/a.png", width=20, height=10, processing_status="succeeded"),
        ManualAsset(version_id=version.id, page_number=2, asset_index=0, sha256="a" * 64,
                    file_path="assets/a.png", width=20, height=10, processing_status="succeeded"),
    ])
    with pytest.raises(IntegrityError):
        session.commit()


def test_ocr_defaults_do_not_require_credentials(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("VISION_API_KEY", raising=False)
    settings = Settings()
    assert settings.ocr_enabled is True
    assert settings.ocr_language == "ch"
    assert settings.vision_enabled is False


def test_citation_defaults_to_pdf_text_without_asset():
    from app.schemas import Citation

    citation = Citation(manual_title="Test manual", chapter_title="Page 1", page_number=1, excerpt="text")
    assert citation.asset_id is None
    assert citation.evidence_type == "pdf_text"


def test_anonymous_cannot_access_knowledge():
    with TestClient(app) as client:
        assert client.get("/vehicles").status_code == 401
        assert client.post("/chat", json={"vehicle_id": "byd-seal", "question": "test"}).status_code == 401


@pytest.fixture
def system(tmp_path, monkeypatch):
    from app.db import Base, get_engine, get_session, session_factory
    from app.models import User
    from app.auth import hash_password

    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "test.db"))
    monkeypatch.setenv("JWT_SECRET", "test-only-secret-with-more-than-32-characters")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "manuals"))
    get_engine.cache_clear()
    session_factory.cache_clear()
    Base.metadata.create_all(get_engine())
    with session_factory()() as session:
        session.add_all([
            User(username="admin", password_hash=hash_password("password-admin"), role="admin"),
            User(username="reader", password_hash=hash_password("password-reader"), role="reader"),
        ])
        session.commit()
    # Queue is external; durable queued rows are exercised by process_job below.
    monkeypatch.setattr("app.management.dispatch_job", lambda job_id: None)
    with TestClient(app) as client:
        admin = client.post("/auth/login", json={"username": "admin", "password": "password-admin"})
        reader = client.post("/auth/login", json={"username": "reader", "password": "password-reader"})
        assert admin.status_code == reader.status_code == 200
        yield client, {"Authorization": "Bearer " + admin.json()["access_token"]}, {
            "Authorization": "Bearer " + reader.json()["access_token"]}
    get_engine().dispose()
    get_engine.cache_clear()
    session_factory.cache_clear()


def pdf_bytes(text="Demonstration tire pressure information."):
    # Small self-authored PDF with a real text stream; no copyrighted fixture.
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 250 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    data = BytesIO()
    writer.write(data)
    return data.getvalue()


def upload(client, headers, text="First manual text", title="Demo manual"):
    return client.post("/admin/manuals", headers=headers,
                       data={"vehicle_id": "toyota-corolla", "title": title, "source": "Self-authored demo"},
                       files={"file": ("demo.pdf", pdf_bytes(text), "application/pdf")})


def test_roles_login_and_expired_token(system):
    import jwt
    client, admin, reader = system
    assert client.get("/vehicles", headers=reader).status_code == 200
    assert client.get("/admin/manuals", headers=reader).status_code == 403
    assert upload(client, reader).status_code == 403
    assert client.post("/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    expired = jwt.encode({"sub": "admin", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
                         "test-only-secret-with-more-than-32-characters", algorithm="HS256")
    assert client.get("/vehicles", headers={"Authorization": "Bearer " + expired}).status_code == 401
    result = client.post("/admin/users", headers=admin,
                         json={"username": "new-reader", "password": "long-password", "role": "reader"})
    assert result.status_code == 201
    assert "password_hash" not in result.json()


def test_duplicate_upload_and_version_ids(system):
    client, admin, _ = system
    first = upload(client, admin)
    assert first.status_code == 202
    duplicate = upload(client, admin)
    assert duplicate.json()["version_id"] == first.json()["version_id"]
    other = upload(client, admin, title="Other manual")
    assert other.json()["version_id"] != first.json()["version_id"]
    assert len(client.get("/admin/manuals", headers=admin).json()) == 2


def test_import_activation_failure_disable_and_original(system, monkeypatch):
    from app.importing import process_job
    from app.management import active_version_ids
    client, admin, reader = system
    chunks = {}
    class Store(ImportStoreStub):
        def upsert(self, values):
            chunks.update({chunk.id: chunk for chunk in values})
    monkeypatch.setattr("app.importing.ManualStore", Store)
    first = upload(client, admin).json()
    process_job(first["job_id"])
    assert client.get("/jobs/" + first["job_id"], headers=admin).json()["status"] == "succeeded"
    assert active_version_ids("toyota-corolla") == {first["version_id"]}
    process_job(first["job_id"])
    assert len(chunks) == 1
    assert next(iter(chunks.values())).metadata["version_id"] == first["version_id"]
    bad = client.post(f'/admin/manuals/{first["manual_id"]}/versions', headers=admin,
                      files={"file": ("bad.pdf", b"%PDF-1.4 corrupt", "application/pdf")}).json()
    process_job(bad["job_id"])
    assert client.get("/jobs/" + bad["job_id"], headers=admin).json()["status"] == "failed"
    assert active_version_ids("toyota-corolla") == {first["version_id"]}
    path = f'/manuals/{first["manual_id"]}/versions/{first["version_id"]}/file'
    assert client.get(path).status_code == 401
    assert client.get(path, headers=reader).content.startswith(b"%PDF")
    assert client.patch("/admin/manuals/" + first["manual_id"], headers=admin,
                        json={"enabled": False}).status_code == 200
    assert active_version_ids("toyota-corolla") == set()


def test_failed_embedding_never_activates_partial_version(system, monkeypatch):
    from app.importing import process_job
    from app.management import active_version_ids
    client, admin, _ = system
    class BrokenStore(ImportStoreStub):
        def upsert(self, values):
            raise ConnectionError("temporary")
    monkeypatch.setattr("app.importing.ManualStore", BrokenStore)
    result = upload(client, admin).json()
    for _ in range(4):
        process_job(result["job_id"])
    job = client.get("/jobs/" + result["job_id"], headers=admin).json()
    assert job["status"] == "failed"
    assert job["attempts"] == 4
    assert not active_version_ids("toyota-corolla")
    retry = client.post("/jobs/" + result["job_id"] + "/retry", headers=admin)
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"


def test_retrieval_excludes_inactive_versions_even_if_store_returns_them():
    from app.rag.store import ManualStore
    class Collection:
        def count(self):
            return 2
        def get(self, **kwargs):
            return {"ids": ["a", "b"], "documents": ["active", "old"],
                    "metadatas": [
                        {"vehicle_id": "toyota-corolla", "version_id": "active"},
                        {"vehicle_id": "toyota-corolla", "version_id": "old"}]}
    store = ManualStore(collection=Collection(), version_ids={"active"})
    assert [c.text for c in store.list_chunks("toyota-corolla")] == ["active"]


def test_same_title_different_versions_keep_both_citations():
    from app.rag.answering import _citations_from_evidence
    from app.rag.store import RetrievedChunk
    evidence = [RetrievedChunk(id=v, text=v, distance=0.1, metadata={
        "manual_title": "Demo", "manual_id": v, "version_id": v,
        "chapter_title": "Page", "page_number": 1}) for v in ["v1", "v2"]]
    citations = _citations_from_evidence(evidence, ["v1", "v2"])
    assert len(citations) == 2
    assert citations[0].version_id == "v1"


def test_duplicate_workers_claim_job_once(system, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.importing import process_job
    client, admin, _ = system
    entered, release = Event(), Event()
    stored = []
    class SlowStore(ImportStoreStub):
        def upsert(self, chunks):
            entered.set()
            assert release.wait(10)
            stored.extend(chunks)
    monkeypatch.setattr("app.importing.ManualStore", SlowStore)
    job = upload(client, admin).json()
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(process_job, job["job_id"])
        assert entered.wait(10)
        try:
            pool.submit(process_job, job["job_id"]).result(timeout=10)
        finally:
            release.set()
        first.result(timeout=10)
    result = client.get("/jobs/" + job["job_id"], headers=admin).json()
    assert result["status"] == "succeeded"
    assert result["attempts"] == 1
    assert len(stored) == 1


def test_older_slow_import_cannot_replace_newer_success(system, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.importing import process_job
    from app.management import active_version_ids
    client, admin, _ = system
    entered, release = Event(), Event()
    class Store(ImportStoreStub):
        def upsert(self, chunks):
            if chunks[0].text == "old":
                entered.set()
                assert release.wait(10)
    monkeypatch.setattr("app.importing.ManualStore", Store)
    old = upload(client, admin, text="old").json()
    new = upload(client, admin, text="new").json()
    with ThreadPoolExecutor(2) as pool:
        task = pool.submit(process_job, old["job_id"])
        assert entered.wait(10)
        try:
            process_job(new["job_id"])
            assert active_version_ids("toyota-corolla") == {new["version_id"]}
        finally:
            release.set()
        task.result(timeout=10)
    assert active_version_ids("toyota-corolla") == {new["version_id"]}


@pytest.mark.parametrize("status", ["embedding", "ocr"])
def test_recovery_fences_stale_worker_and_caps_attempts(system, status):
    from app.importing import recover_jobs, _progress, LeaseLost
    from app.models import ImportJob, utcnow
    from app.db import session_factory
    client, admin, _ = system
    first = upload(client, admin).json()
    exhausted = upload(client, admin, title="exhausted").json()
    with session_factory()() as session:
        for data, attempts in [(first, 1), (exhausted, 4)]:
            job = session.get(ImportJob, data["job_id"])
            job.status, job.attempts, job.lease_token = status, attempts, "stale"
            job.updated_at = utcnow() - timedelta(hours=1)
        session.commit()
    recover_jobs()
    assert client.get("/jobs/" + first["job_id"], headers=admin).json()["status"] == "queued"
    assert client.get("/jobs/" + exhausted["job_id"], headers=admin).json()["status"] == "failed"
    with pytest.raises(LeaseLost):
        _progress(first["job_id"], "stale", "embedding")


def test_inflight_answer_discards_disabled_version(system, monkeypatch):
    from app.importing import process_job
    from app.rag.store import RetrievedChunk
    from app.schemas import ChatResponse
    client, admin, reader = system
    class Store(ImportStoreStub):
        def upsert(self, chunks):
            pass
    monkeypatch.setattr("app.importing.ManualStore", Store)
    job = upload(client, admin).json()
    process_job(job["job_id"])
    chunk = RetrievedChunk(id="one", text="Manual text", distance=0.1, metadata={
        "manual_id": job["manual_id"], "version_id": job["version_id"], "vehicle_id": "toyota-corolla"})
    monkeypatch.setattr("app.api.routes.ManualStore", lambda **_: object())
    monkeypatch.setattr("app.api.routes.retrieve_evidence", lambda *a, **kw: [chunk])
    def answer(question, vehicle, evidence):
        if evidence:
            client.patch("/admin/manuals/" + job["manual_id"], headers=admin, json={"enabled": False})
        return ChatResponse(answer="supported" if evidence else "no evidence",
                            steps=[], warnings=[], citations=[], grounded=bool(evidence))
    monkeypatch.setattr("app.api.routes.answer_question", answer)
    result = client.post("/chat", headers=reader, json={"vehicle_id": "toyota-corolla", "question": "test"})
    assert result.status_code == 200
    assert result.json()["grounded"] is False


def test_reader_cannot_manage_jobs_or_accounts_and_pdf_ids_must_match(system):
    client, admin, reader = system
    first, other = upload(client, admin).json(), upload(client, admin, title="Other").json()
    assert client.get("/jobs/" + first["job_id"], headers=reader).status_code == 403
    assert client.post("/jobs/" + first["job_id"] + "/retry", headers=reader).status_code == 403
    assert client.get("/admin/users", headers=reader).status_code == 403
    assert client.post("/admin/users", headers=reader, json={
        "username": "hacker", "password": "long-password", "role": "admin"}).status_code == 403
    path = f'/manuals/{other["manual_id"]}/versions/{first["version_id"]}/file'
    response = client.get(path, headers=reader)
    assert response.status_code == 404
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.parametrize("failure_kind", ["server", "wrapped_connection", "rate_limit"])
def test_transient_chroma_server_error_requeues_job(system, monkeypatch, failure_kind):
    import httpx
    from app.importing import process_job
    client, admin, _ = system
    class UnavailableStore(ImportStoreStub):
        def upsert(self, chunks):
            if failure_kind == "wrapped_connection":
                try:
                    raise httpx.ConnectError("unavailable")
                except httpx.ConnectError:
                    raise ValueError("Could not connect to a Chroma server.")
            response = httpx.Response(429 if failure_kind == "rate_limit" else 503,
                                      request=httpx.Request("POST", "http://chroma/"))
            response.raise_for_status()
    monkeypatch.setattr("app.importing.ManualStore", UnavailableStore)
    data = upload(client, admin).json()
    process_job(data["job_id"])
    result = client.get("/jobs/" + data["job_id"], headers=admin).json()
    assert result["status"] == "queued"


def test_repeated_http_stores_share_one_client_and_close_it(monkeypatch):
    from app.rag import store
    import chromadb
    created = []
    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            self.collection = object()
            created.append(self)
        def get_or_create_collection(self, **kwargs):
            return self.collection
        def close(self):
            self.closed = True
    monkeypatch.setenv("CHROMA_HOST", "test-server")
    monkeypatch.setattr(chromadb, "HttpClient", Client)
    store.ManualStore().collection
    store.ManualStore().collection
    assert len(created) == 1
    store.close_shared_clients()
    assert created[0].closed


def image_pdf_bytes(text="Manual text"):
    import pymupdf
    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (20, 10), "white").save(image, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=300)
        if text:
            page.insert_text((20, 30), text)
        page.insert_image(pymupdf.Rect(20, 50, 120, 100), stream=image.getvalue())
        return document.tobytes()


def upload_image_pdf(client, admin, text="Manual text"):
    return client.post("/admin/manuals", headers=admin,
                       data={"vehicle_id": "toyota-corolla", "title": "Illustrated", "source": "Self-authored"},
                       files={"file": ("images.pdf", image_pdf_bytes(text), "application/pdf")}).json()


@pytest.mark.parametrize("text", ["Manual text", ""])
def test_import_ocr_assets_and_reprocessing_are_idempotent(system, monkeypatch, text):
    from pathlib import Path
    from sqlalchemy import select
    from app.config import Settings
    from app.db import session_factory
    from app.importing import process_job
    from app.models import ImportJob, ManualAsset, ManualVersion
    from app.rag.visual import RapidOCRClient

    client, admin, _ = system
    vectors = {}
    class Store(ImportStoreStub):
        def upsert(self, chunks):
            assert len(chunks) <= 64
            vectors.update({chunk.id: chunk for chunk in chunks})
    def recognize(self, data):
        from PIL import Image
        with Image.open(BytesIO(data)) as image:
            return "Image tire label" if image.size == (20, 10) else "Scanned manual page"
    monkeypatch.setattr("app.importing.ManualStore", Store)
    monkeypatch.setattr(RapidOCRClient, "recognize", recognize)
    data = upload_image_pdf(client, admin, text)
    process_job(data["job_id"])
    first_ids = set(vectors)
    assert client.get("/jobs/" + data["job_id"], headers=admin).json()["status"] == "succeeded"
    with session_factory()() as session:
        assets = list(session.scalars(select(ManualAsset)))
        assert len(assets) == 1
        asset = assets[0]
        asset_id = asset.id
        assert asset.ocr_text == "Image tire label"
        assert asset.processing_status == "succeeded"
        root = Path(Settings().storage_path).resolve()
        path = (root / asset.file_path).resolve()
        assert path.is_relative_to(root) and path.is_file()
        assert data["version_id"] in asset.file_path
        import hashlib
        assert asset.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        session.get(ImportJob, data["job_id"]).status = "queued"
        session.commit()
    process_job(data["job_id"])
    with session_factory()() as session:
        assert [a.id for a in session.scalars(select(ManualAsset))] == [asset_id]
        assert session.get(ManualVersion, data["version_id"]).chunk_count == len(first_ids)
    assert set(vectors) == first_ids
    assert {c.metadata.get("evidence_type", "pdf_text") for c in vectors.values()} == {
        "image_ocr", "pdf_text" if text else "page_ocr"}
    for chunk in vectors.values():
        assert chunk.id.startswith(data["version_id"] + "-")
        assert chunk.metadata["vehicle_id"] == "toyota-corolla"
        assert chunk.metadata["manual_id"] == data["manual_id"]
        assert chunk.metadata["version_id"] == data["version_id"]
        assert chunk.metadata["page_number"] == 1
        if chunk.metadata.get("evidence_type") == "image_ocr":
            assert chunk.metadata["asset_id"] == asset_id


def test_image_ocr_failure_does_not_fail_text_import(system, monkeypatch):
    from sqlalchemy import select
    from app.db import session_factory
    from app.importing import process_job
    from app.models import ManualAsset
    from app.rag.visual import RapidOCRClient

    client, admin, _ = system
    chunks = []
    class Store(ImportStoreStub):
        def upsert(self, values):
            chunks.extend(values)
    def fail(self, data):
        raise RuntimeError("OCR unavailable")
    monkeypatch.setattr("app.importing.ManualStore", Store)
    monkeypatch.setattr(RapidOCRClient, "recognize", fail)
    data = upload_image_pdf(client, admin)
    process_job(data["job_id"])
    assert client.get("/jobs/" + data["job_id"], headers=admin).json()["status"] == "succeeded"
    assert [c.text for c in chunks] == ["Manual text"]
    with session_factory()() as session:
        asset = session.scalar(select(ManualAsset))
        assert asset is not None
        assert asset.processing_status == "failed" and asset.error


def test_asset_file_requires_auth_and_matching_ids_and_safe_path(system, tmp_path):
    from pathlib import Path
    from app.config import Settings
    from app.db import session_factory
    from app.models import ManualAsset

    client, admin, reader = system
    first = upload(client, admin).json()
    other = upload(client, admin, title="Other").json()
    root = Path(Settings().storage_path)
    (root / "asset.png").write_bytes(b"image data")
    with session_factory()() as session:
        asset = ManualAsset(version_id=first["version_id"], page_number=1, asset_index=0,
                            sha256="a" * 64, file_path="asset.png", width=20, height=10,
                            processing_status="succeeded")
        session.add(asset)
        session.commit()
        asset_id = asset.id
    path = f'/manuals/{first["manual_id"]}/versions/{first["version_id"]}/assets/{asset_id}/file'
    assert client.get(path).status_code == 401
    response = client.get(path, headers=reader)
    assert response.status_code == 200 and response.content == b"image data"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["cache-control"] == "private, no-store"
    assert client.get(path.replace(first["manual_id"], other["manual_id"]), headers=reader).status_code == 404
    assert client.get(path.replace(first["version_id"], other["version_id"]), headers=reader).status_code == 404
    assert client.get(path.replace(asset_id, "missing"), headers=reader).status_code == 404
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"private")
    for unsafe_path in ["../outside.png", str(outside.resolve()), "missing.png"]:
        with session_factory()() as session:
            session.get(ManualAsset, asset_id).file_path = unsafe_path
            session.commit()
        assert client.get(path, headers=reader).status_code == 404


def test_empty_ocr_pdf_keeps_no_text_failure(system, monkeypatch):
    from app.importing import process_job
    from app.rag.visual import RapidOCRClient

    client, admin, _ = system
    monkeypatch.setattr(RapidOCRClient, "recognize", lambda self, data: "")
    data = upload_image_pdf(client, admin, text="")
    process_job(data["job_id"])
    job = client.get("/jobs/" + data["job_id"], headers=admin).json()
    assert job["status"] == "failed" and job["error"] == "PDF 无可提取文本。"


def test_page_ocr_failure_still_imports_asset_ocr(system, monkeypatch):
    from app.importing import process_job
    from app.rag.visual import RapidOCRClient

    client, admin, _ = system
    chunks = []
    class Store(ImportStoreStub):
        def upsert(self, values):
            chunks.extend(values)
    def recognize(self, data):
        from PIL import Image
        with Image.open(BytesIO(data)) as image:
            if image.size != (20, 10):
                raise RuntimeError("Page recognition failed")
        return "Readable image label"
    monkeypatch.setattr("app.importing.ManualStore", Store)
    monkeypatch.setattr(RapidOCRClient, "recognize", recognize)
    data = upload_image_pdf(client, admin, text="")
    process_job(data["job_id"])
    assert client.get("/jobs/" + data["job_id"], headers=admin).json()["status"] == "succeeded"
    assert [c.text for c in chunks] == ["Readable image label"]
    assert chunks[0].metadata["evidence_type"] == "image_ocr"


@pytest.mark.parametrize("ocr_enabled", [True, False])
def test_text_import_survives_visual_extraction_error_or_disabled_ocr(system, monkeypatch, ocr_enabled):
    from app.importing import process_job

    client, admin, _ = system
    chunks = []
    class Store(ImportStoreStub):
        def upsert(self, values):
            chunks.extend(values)
    def fail(*args, **kwargs):
        if not ocr_enabled:
            pytest.fail("Disabled OCR must not extract images")
        raise RuntimeError("Visual extraction failed")
    monkeypatch.setenv("OCR_ENABLED", str(ocr_enabled).lower())
    monkeypatch.setattr("app.importing.extract_visual_pages", fail)
    monkeypatch.setattr("app.importing.ManualStore", Store)
    data = upload(client, admin).json()
    process_job(data["job_id"])
    assert client.get("/jobs/" + data["job_id"], headers=admin).json()["status"] == "succeeded"
    assert [c.text for c in chunks] == ["First manual text"]


def test_asset_persistence_rejects_path_escape(session, tmp_path):
    from app.importing import persist_visual_assets
    from app.rag.visual import ExtractedAsset, VisualPage

    version = make_version(session)
    source = ExtractedAsset(1, 0, b"image", "../../outside.png", 20, 10)
    with pytest.raises(ValueError):
        persist_visual_assets(session, version, [VisualPage(1, "", [source])], tmp_path)
    assert not (tmp_path / "assets").exists()


def test_ocr_retry_after_partial_vector_write_reuses_assets_and_batches(system, monkeypatch):
    from sqlalchemy import select
    from app.db import session_factory
    from app.importing import process_job
    from app.models import ManualAsset, ManualVersion
    from app.rag.visual import RapidOCRClient

    client, admin, _ = system
    vectors, batch_sizes = {}, []
    class Store(ImportStoreStub):
        def upsert(self, chunks):
            batch_sizes.append(len(chunks))
            if len(batch_sizes) == 2:
                raise ConnectionError("Interrupted vector write")
            vectors.update({c.id: c for c in chunks})
    monkeypatch.setattr("app.importing.ManualStore", Store)
    monkeypatch.setattr(RapidOCRClient, "recognize", lambda self, data: "label " * 10000)
    data = upload_image_pdf(client, admin)
    process_job(data["job_id"])
    assert client.get("/jobs/" + data["job_id"], headers=admin).json()["status"] == "queued"
    assert len(vectors) == 64
    with session_factory()() as session:
        first_asset_ids = list(session.scalars(select(ManualAsset.id)))
        assert len(first_asset_ids) == 1
    process_job(data["job_id"])
    assert client.get("/jobs/" + data["job_id"], headers=admin).json()["status"] == "succeeded"
    # 77 overlapping OCR chunks plus the original PDF-text chunk.
    assert batch_sizes == [64, 14, 64, 14]
    assert len(vectors) == 78
    with session_factory()() as session:
        assert list(session.scalars(select(ManualAsset.id))) == first_asset_ids
        assert session.get(ManualVersion, data["version_id"]).chunk_count == 78


@pytest.mark.parametrize("retry_page_text", ["", "Replacement page OCR"])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_ocr_retry_removes_stale_vectors_without_touching_other_versions(
        system, monkeypatch, retry_page_text, cleanup_failure):
    import chromadb
    from app.db import session_factory
    from app.importing import process_job
    from app.management import active_version_ids
    from app.models import ManualVersion, new_id
    from app.rag.store import ManualStore
    from app.rag.visual import RapidOCRClient

    client, admin, _ = system
    collection = chromadb.EphemeralClient().create_collection("retry-" + new_id())
    class Embedding:
        def encode(self, values):
            return [[1.0, 0.0] for _ in values]
    class Store(ManualStore):
        batches = 0
        interrupt = False
        fail_cleanup = False
        def upsert(self, chunks):
            self.batches += 1
            if self.interrupt and self.batches == 2:
                raise ConnectionError("Interrupted vector write")
            super().upsert(chunks)
        def reconcile_version(self, version_id, chunk_ids):
            if self.fail_cleanup:
                self.fail_cleanup = False
                raise ConnectionError("Interrupted cleanup")
            super().reconcile_version(version_id, chunk_ids)
    store = Store(collection=collection, embedding_model=Embedding())
    monkeypatch.setattr("app.importing.ManualStore", lambda: store)
    old = upload(client, admin, text="Active manual evidence", title="Illustrated").json()
    process_job(old["job_id"])
    old_vectors = collection.get(where={"version_id": old["version_id"]}, include=["documents", "metadatas"])
    assert old_vectors["documents"] == ["Active manual evidence"]
    page_text = "page OCR " * 10000
    def recognize(self, data):
        from PIL import Image
        with Image.open(BytesIO(data)) as image:
            return "Image label" if image.size == (20, 10) else page_text
    monkeypatch.setattr(RapidOCRClient, "recognize", recognize)
    new = upload_image_pdf(client, admin, text="")
    store.batches, store.interrupt = 0, True
    process_job(new["job_id"])
    assert client.get("/jobs/" + new["job_id"], headers=admin).json()["status"] == "queued"
    assert len(collection.get(where={"version_id": new["version_id"]})["ids"]) == 64
    assert active_version_ids("toyota-corolla") == {old["version_id"]}
    page_text, store.interrupt = retry_page_text, False
    store.fail_cleanup = cleanup_failure
    if cleanup_failure:
        process_job(new["job_id"])
        assert client.get("/jobs/" + new["job_id"], headers=admin).json()["status"] == "queued"
        assert active_version_ids("toyota-corolla") == {old["version_id"]}
    process_job(new["job_id"])
    assert client.get("/jobs/" + new["job_id"], headers=admin).json()["status"] == "succeeded"
    current = collection.get(where={"version_id": new["version_id"]}, include=["documents", "metadatas"])
    assert sorted(current["documents"]) == sorted(["Image label"] + ([retry_page_text] if retry_page_text else []))
    with session_factory()() as session:
        assert session.get(ManualVersion, new["version_id"]).chunk_count == len(current["ids"])
    assert collection.get(where={"version_id": old["version_id"]}, include=["documents", "metadatas"]) == old_vectors
    assert active_version_ids("toyota-corolla") == {new["version_id"]}
