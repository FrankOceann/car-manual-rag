from typing import Annotated, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.catalog import get_vehicle, list_vehicles
from app.rag.answering import answer_question
from app.rag.retriever import retrieve_evidence
from app.rag.store import ManualStore
from app.schemas import ChatResponse, Vehicle


router = APIRouter()


class ChatRequest(BaseModel):
    vehicle_id: str
    question: Annotated[str, Field(min_length=1)]


@router.get("/vehicles", response_model=list[Vehicle])
def vehicles() -> list[Vehicle]:
    return list(list_vehicles())


@router.get("/manuals/{vehicle_id}/chapters", response_model=list[str])
def manual_chapters(vehicle_id: str) -> list[str]:
    if get_vehicle(vehicle_id) is None:
        raise HTTPException(status_code=422, detail="Unsupported vehicle.")

    rows = _manual_collection().get(
        where={"vehicle_id": vehicle_id}, include=["metadatas"]
    )
    return list(
        dict.fromkeys(
            str(metadata["chapter_title"])
            for metadata in rows.get("metadatas", [])
            if metadata.get("chapter_title")
        )
    )


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    vehicle = get_vehicle(request.vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=422, detail="Unsupported vehicle.")
    if not request.question.strip():
        raise HTTPException(status_code=422, detail="Question must not be blank.")

    evidence = retrieve_evidence(vehicle.id, request.question)
    try:
        return answer_question(request.question, vehicle, evidence)
    except RuntimeError as error:
        if "DeepSeek API key is not configured" in str(error):
            raise HTTPException(status_code=503, detail="DeepSeek is not configured.") from error
        raise


def _manual_collection() -> Any:
    return ManualStore().collection
