from typing import Any

from fastapi import APIRouter, HTTPException, Depends
from app.auth import current_user
from app.management import active_version_ids
import logging
from time import perf_counter

from app.catalog import get_vehicle, list_vehicles
from app.rag.answering import answer_question
from app.rag.retriever import RetrievalOptions, retrieve_evidence
from app.rag.store import ManualStore
from app.schemas import ChatRequest, ChatResponse, Vehicle


router = APIRouter(dependencies=[Depends(current_user)])
log = logging.getLogger("rag")

@router.get("/vehicles", response_model=list[Vehicle])
def vehicles() -> list[Vehicle]:
    return list(list_vehicles())


@router.get("/manuals/{vehicle_id}/chapters", response_model=list[str])
def manual_chapters(vehicle_id: str) -> list[str]:
    if get_vehicle(vehicle_id) is None:
        raise HTTPException(status_code=422, detail="Unsupported vehicle.")

    ids = active_version_ids(vehicle_id)
    if ids == set():
        return []
    where = {"vehicle_id": vehicle_id} if ids is None else {
        "$and": [{"vehicle_id": vehicle_id}, {"version_id": {"$in": sorted(ids)}}]}
    rows = _manual_collection().get(where=where, include=["metadatas"])
    return list(
        dict.fromkeys(
            str(metadata["chapter_title"])
            for metadata in rows.get("metadatas", [])
            if metadata.get("chapter_title")
            and (ids is None or metadata.get("version_id") in ids)
        )
    )


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    vehicle = get_vehicle(request.vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=422, detail="Unsupported vehicle.")
    if not request.question.strip():
        raise HTTPException(status_code=422, detail="Question must not be blank.")

    started = perf_counter()
    ids = active_version_ids(vehicle.id)
    evidence = [] if ids == set() else retrieve_evidence(
        vehicle.id,
        request.question,
        options=RetrievalOptions(
            chapter_titles=set(request.chapter_titles)
            if request.chapter_titles is not None
            else None
        ),
        store=ManualStore(version_ids=ids),
    )
    log.info("retrieval_completed", extra={"duration_ms": round((perf_counter()-started)*1000)})
    started = perf_counter()
    try:
        answer = answer_question(request.question, vehicle, evidence)
        # An administrator may have disabled/switched a version while the model was answering.
        latest = active_version_ids(vehicle.id)
        if latest is not None and any(chunk.metadata.get("version_id") not in latest for chunk in evidence):
            return answer_question(request.question, vehicle, [])
        return answer
    except RuntimeError as error:
        if "DeepSeek API key is not configured" in str(error):
            raise HTTPException(status_code=503, detail="DeepSeek is not configured.") from error
        raise
    finally:
        log.info("generation_completed", extra={"duration_ms": round((perf_counter()-started)*1000)})


def _manual_collection() -> Any:
    return ManualStore().collection
