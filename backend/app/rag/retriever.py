from dataclasses import dataclass

from app.rag.store import ManualStore, RetrievedChunk


@dataclass(frozen=True)
class RetrievalOptions:
    limit: int = 4
    chapter_titles: frozenset[str] | None = None
    minimum_distance: float = 1.1

    def __post_init__(self) -> None:
        if self.chapter_titles is not None:
            object.__setattr__(self, "chapter_titles", frozenset(self.chapter_titles))


def retrieve_evidence(
    vehicle_id: str,
    question: str,
    options: RetrievalOptions | None = None,
    *,
    store: ManualStore | None = None,
) -> list[RetrievedChunk]:
    """Return only vehicle-scoped chunks sufficiently close to the question."""
    manual_store = store or ManualStore()
    retrieval_options = options or RetrievalOptions()
    if retrieval_options.chapter_titles == frozenset():
        return []

    candidate_limit = (
        manual_store.collection.count()
        if retrieval_options.chapter_titles is not None
        else retrieval_options.limit
    )
    evidence = [
        chunk
        for chunk in manual_store.query(vehicle_id, question, candidate_limit)
        if chunk.distance <= retrieval_options.minimum_distance
        and (
            retrieval_options.chapter_titles is None
            or chunk.metadata.get("chapter_title") in retrieval_options.chapter_titles
        )
    ]
    return evidence[: retrieval_options.limit]
