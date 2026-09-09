from fastapi.testclient import TestClient

from app.main import app
from app.rag.store import RetrievedChunk


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


def test_chat_returns_ungrounded_when_retrieval_is_empty(monkeypatch):
    monkeypatch.setattr("app.api.routes.retrieve_evidence", lambda *_: [])

    response = TestClient(app).post(
        "/chat", json={"vehicle_id": "toyota-corolla", "question": "保养周期"}
    )

    assert response.status_code == 200
    assert response.json()["grounded"] is False


def test_chat_returns_503_when_deepseek_is_not_configured(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.retrieve_evidence", lambda *_: sample_evidence()
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
