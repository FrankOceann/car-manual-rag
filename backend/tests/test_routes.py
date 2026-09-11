import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rag.store import ManualStore, RetrievedChunk
from app.auth import current_user
from app.models import User


@pytest.fixture(autouse=True)
def authenticated_legacy_route_contract(monkeypatch):
    # These tests isolate the established RAG contract; enterprise tests exercise real auth and DB.
    app.dependency_overrides[current_user] = lambda: User(id="test", username="reader", role="reader", active=True)
    monkeypatch.setattr("app.api.routes.active_version_ids", lambda _: None)
    yield
    app.dependency_overrides.pop(current_user, None)


def sample_evidence() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            id="corolla-tires",
            text="轮胎压力警告灯亮起时，请检查所有轮胎压力。",
            metadata={
                "vehicle_id": "toyota-corolla",
                "manual_title": "2024 卡罗拉用户手册",
                "chapter_title": "轮胎",
                "page_number": 187,
            },
            distance=0.2,
        )
    ]


def test_vehicles_endpoint_returns_three_models():
    response = TestClient(app).get("/vehicles")

    assert response.status_code == 200
    assert len(response.json()) == 3


def test_chat_rejects_an_unsupported_vehicle():
    response = TestClient(app).post(
        "/chat", json={"vehicle_id": "unknown", "question": "保养周期"}
    )

    assert response.status_code == 422


def test_manual_chapters_returns_unique_titles_for_a_vehicle(monkeypatch):
    class Collection:
        def get(self, **_):
            return {
                "metadatas": [
                    {"chapter_title": "轮胎"},
                    {"chapter_title": "保养"},
                    {"chapter_title": "轮胎"},
                ]
            }

    monkeypatch.setattr("app.api.routes._manual_collection", lambda: Collection())

    response = TestClient(app).get("/manuals/toyota-corolla/chapters")

    assert response.status_code == 200
    assert response.json() == ["轮胎", "保养"]


def test_manual_chapters_returns_empty_without_loading_an_embedding_model(
    monkeypatch, tmp_path
):
    pytest.importorskip("chromadb")
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path))

    def embedding_model_must_not_be_loaded():
        raise AssertionError("chapter listing must not load an embedding model")

    monkeypatch.setattr(
        ManualStore,
        "_create_embedding_model",
        staticmethod(embedding_model_must_not_be_loaded),
    )

    response = TestClient(app).get("/manuals/toyota-corolla/chapters")

    assert response.status_code == 200
    assert response.json() == []


def test_chat_returns_ungrounded_when_retrieval_is_empty(monkeypatch):
    monkeypatch.setattr("app.api.routes.retrieve_evidence", lambda *args, **kwargs: [])

    response = TestClient(app).post(
        "/chat", json={"vehicle_id": "toyota-corolla", "question": "保养周期"}
    )

    assert response.status_code == 200
    assert response.json()["grounded"] is False


def test_chat_returns_503_when_deepseek_is_not_configured(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.retrieve_evidence", lambda *args, **kwargs: sample_evidence()
    )

    def missing_configuration(*_):
        raise RuntimeError("DeepSeek API key is not configured.")

    monkeypatch.setattr("app.api.routes.answer_question", missing_configuration)

    response = TestClient(app).post(
        "/chat", json={"vehicle_id": "toyota-corolla", "question": "胎压警告"}
    )

    assert response.status_code == 503


def test_chat_rejects_a_blank_question():
    response = TestClient(app).post(
        "/chat", json={"vehicle_id": "toyota-corolla", "question": "   "}
    )

    assert response.status_code == 422


def test_chat_passes_selected_chapters_to_retrieval(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.api.routes.retrieve_evidence",
        lambda *args, **kwargs: captured.update(kwargs) or [],
    )

    response = TestClient(app).post(
        "/chat",
        json={
            "vehicle_id": "toyota-corolla",
            "question": "轮胎",
            "chapter_titles": ["轮胎"],
        },
    )

    assert response.status_code == 200
    assert captured["options"].chapter_titles == {"轮胎"}


def test_chat_rejects_blank_chapter_titles(monkeypatch):
    monkeypatch.setattr("app.api.routes.retrieve_evidence", lambda *args, **kwargs: [])

    response = TestClient(app).post(
        "/chat",
        json={
            "vehicle_id": "toyota-corolla",
            "question": "轮胎",
            "chapter_titles": ["   "],
        },
    )

    assert response.status_code == 422


def test_cors_allows_the_local_frontend_origin():
    response = TestClient(app).options(
        "/chat",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_allows_the_loopback_frontend_origin():
    response = TestClient(app).options(
        "/chat",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_cors_denies_a_foreign_origin():
    response = TestClient(app).options(
        "/chat",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
