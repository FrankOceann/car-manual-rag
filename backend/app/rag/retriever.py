from dataclasses import dataclass

from app.rag.store import ManualStore, RetrievedChunk


@dataclass(frozen=True)
class RetrievalOptions:
    limit: int = 4
    chapter_titles: set[str] | None = None
    minimum_distance: float = 1.1


def retrieve_evidence(
    vehicle_id: str,
    question: str,
    minimum_distance: float = 1.1,
    *,
    store: ManualStore | None = None,
) -> list[RetrievedChunk]:
    """Return only vehicle-scoped chunks sufficiently close to the question."""
    manual_store = store or ManualStore()
    return [
        chunk
        for chunk in manual_store.query(vehicle_id, question)
        if chunk.distance <= minimum_distance
    ]
