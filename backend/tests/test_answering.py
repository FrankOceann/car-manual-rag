from types import SimpleNamespace

import pytest

from app.config import Settings
from app.schemas import Vehicle
from app.rag.answering import AnswerService
from app.rag.store import RetrievedChunk


@pytest.fixture
def corolla():
    return Vehicle(id="toyota-corolla", brand="丰田", model="卡罗拉", year=2024)


@pytest.fixture
def evidence():
    return [
        RetrievedChunk(
            id="chunk-1",
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


@pytest.fixture
def client():
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content='{"answer":"请检查轮胎压力。","steps":["检查所有轮胎压力"],"warnings":["如警告持续，请联系服务人员"]}'
                            )
                        )
                    ]
                )
            )
        )
    )


@pytest.fixture
def service(client):
    return AnswerService(
        settings=Settings(deepseek_api_key="test-key"),
        client=client,
    )


def test_no_evidence_returns_an_ungrounded_response(service, corolla):
    result = service.answer_question("怎样拆卸变速箱？", corolla, [])

    assert result.grounded is False
    assert result.steps == []
    assert result.citations == []


def test_grounded_response_contains_citations_from_evidence(service, corolla, evidence):
    result = service.answer_question("胎压警告是什么意思？", corolla, evidence)

    assert result.grounded is True
    assert result.citations[0].page_number == evidence[0].metadata["page_number"]
    assert result.citations[0].excerpt == evidence[0].text


def test_missing_api_key_raises_a_safe_configuration_error(corolla, evidence):
    service = AnswerService(settings=Settings(deepseek_api_key=None))

    with pytest.raises(RuntimeError, match="DeepSeek API key"):
        service.answer_question("胎压警告是什么意思？", corolla, evidence)


def test_malformed_model_output_returns_an_ungrounded_response(service, corolla, evidence):
    service.client.chat.completions.create = lambda **_: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))]
    )

    result = service.answer_question("胎压警告是什么意思？", corolla, evidence)

    assert result.grounded is False
    assert result.steps == []
    assert result.citations == []
