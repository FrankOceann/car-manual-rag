from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app.main import app


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
    class Store:
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
    class BrokenStore:
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
    class SlowStore:
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
    class Store:
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


def test_recovery_fences_stale_worker_and_caps_attempts(system):
    from app.importing import recover_jobs, _progress, LeaseLost
    from app.models import ImportJob, utcnow
    from app.db import session_factory
    client, admin, _ = system
    first = upload(client, admin).json()
    exhausted = upload(client, admin, title="exhausted").json()
    with session_factory()() as session:
        for data, attempts in [(first, 1), (exhausted, 4)]:
            job = session.get(ImportJob, data["job_id"])
            job.status, job.attempts, job.lease_token = "embedding", attempts, "stale"
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
    class Store:
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
    class UnavailableStore:
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
