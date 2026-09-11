from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from app.config import Settings
from app.rag.store import RetrievedChunk
from app.schemas import ChatResponse, Citation, Vehicle

NO_EVIDENCE_ANSWER = "当前检索到的手册内容不足以回答这个问题，请查阅对应车型的官方手册或咨询专业服务人员。"
MALFORMED_RESPONSE_ANSWER = "无法根据检索到的手册内容生成可靠回答，请查阅对应车型的官方手册或咨询专业服务人员。"


class AnswerService:
    """Generate answers whose claims are limited to retrieved manual excerpts."""

    def __init__(self, settings: Settings | None = None, client: Any | None = None):
        self.settings = settings or Settings()
        self.client = client

    def answer_question(
        self,
        question: str,
        vehicle: Vehicle,
        evidence: list[RetrievedChunk],
    ) -> ChatResponse:
        if not evidence:
            return _safe_response(NO_EVIDENCE_ANSWER)
        if not self.settings.deepseek_api_key:
            raise RuntimeError("DeepSeek API key is not configured.")

        client = self.client or OpenAI(
            base_url=self.settings.deepseek_base_url,
            api_key=self.settings.deepseek_api_key,
            timeout=self.settings.deepseek_timeout,
            max_retries=1,
        )
        completion = client.chat.completions.create(
            model=self.settings.deepseek_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _system_prompt(vehicle, evidence)},
                {"role": "user", "content": question},
            ],
        )
        parsed = _parse_content(_completion_content(completion))
        if parsed is None:
            return _safe_response(MALFORMED_RESPONSE_ANSWER)

        return ChatResponse(
            answer=parsed["answer"],
            steps=parsed["steps"],
            warnings=parsed["warnings"],
            citations=_citations_from_evidence(evidence, parsed["citation_ids"]),
            grounded=True,
        )


def answer_question(
    question: str,
    vehicle: Vehicle,
    evidence: list[RetrievedChunk],
) -> ChatResponse:
    """Answer a question using only the supplied, vehicle-scoped evidence."""
    return AnswerService().answer_question(question, vehicle, evidence)


def _system_prompt(vehicle: Vehicle, evidence: list[RetrievedChunk]) -> str:
    excerpts = [
        {
            "chunk_id": chunk.id,
            "manual_title": chunk.metadata.get("manual_title"),
            "chapter_title": chunk.metadata.get("chapter_title"),
            "page_number": chunk.metadata.get("page_number"),
            "excerpt": chunk.text,
        }
        for chunk in evidence
    ]
    return (
        "你是车辆手册助手。只可依据下方提供的手册摘录回答；不得编造数值、步骤、"
        "部件状态或任何未在摘录中出现的程序。若摘录不能支持回答，请明确说明。"
        "使用中性、安全的语言，不要建议危险操作。只返回 JSON 对象，且只能包含 "
        '"answer"（字符串）、"steps"（字符串数组）、"warnings"（字符串数组）和 '
        '"citation_ids"（仅列出支持回答的 chunk_id 的字符串数组）。'
        f"\n车型：{vehicle.brand}{vehicle.model} {vehicle.year}\n手册摘录："
        f"{json.dumps(excerpts, ensure_ascii=False)}"
    )


def _parse_content(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    answer = parsed.get("answer")
    steps = parsed.get("steps")
    warnings = parsed.get("warnings")
    if (
        not isinstance(answer, str)
        or not isinstance(steps, list)
        or not isinstance(warnings, list)
        or not all(isinstance(item, str) for item in steps)
        or not all(isinstance(item, str) for item in warnings)
    ):
        return None
    citation_ids = parsed.get("citation_ids")
    if not isinstance(citation_ids, list) or not all(
        isinstance(citation_id, str) for citation_id in citation_ids
    ):
        citation_ids = []
    return {
        "answer": answer,
        "steps": steps,
        "warnings": warnings,
        "citation_ids": citation_ids,
    }


def _completion_content(completion: Any) -> Any | None:
    choices = getattr(completion, "choices", None)
    if not isinstance(choices, (list, tuple)) or not choices:
        return None
    message = getattr(choices[0], "message", None)
    return getattr(message, "content", None)


def _citations_from_evidence(
    evidence: list[RetrievedChunk], citation_ids: list[str]
) -> list[Citation]:
    selected_ids = set(citation_ids)
    selected_evidence = [
        chunk for chunk in evidence if chunk.id in selected_ids
    ]
    if not selected_evidence:
        selected_evidence = evidence

    citations = []
    seen_pages = set()
    for chunk in selected_evidence:
        page_key = (
            str(chunk.metadata.get("version_id") or chunk.metadata["manual_title"]),
            int(chunk.metadata["page_number"]),
        )
        if page_key in seen_pages:
            continue
        seen_pages.add(page_key)
        citations.append(
            Citation(
                manual_title=str(chunk.metadata["manual_title"]),
                manual_id=chunk.metadata.get("manual_id"),
                version_id=chunk.metadata.get("version_id"),
                chapter_title=str(chunk.metadata["chapter_title"]),
                page_number=page_key[1],
                excerpt=chunk.text,
            )
        )
    return citations


def _safe_response(answer: str) -> ChatResponse:
    return ChatResponse(
        answer=answer,
        steps=[],
        warnings=[],
        citations=[],
        grounded=False,
    )
