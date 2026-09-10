from dataclasses import dataclass
import re

from rank_bm25 import BM25Okapi
from app.rag.store import ManualStore, RetrievedChunk


@dataclass(frozen=True)
class RetrievalOptions:
    limit: int = 4
    chapter_titles: frozenset[str] | None = None
    minimum_distance: float = 1.1

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be positive")
        object.__setattr__(self, "limit", min(self.limit, 4))
        if self.chapter_titles is not None:
            object.__setattr__(self, "chapter_titles", frozenset(self.chapter_titles))


def retrieve_evidence(
    vehicle_id: str,
    question: str,
    options: RetrievalOptions | None = None,
    *,
    store: ManualStore | None = None,
) -> list[RetrievedChunk]:
    """Return vehicle-scoped evidence reranked with vector and BM25 signals."""
    manual_store = store or ManualStore()
    retrieval_options = options or RetrievalOptions()
    if retrieval_options.chapter_titles == frozenset():
        return []

    candidates = manual_store.list_chunks(
        vehicle_id,
        set(retrieval_options.chapter_titles)
        if retrieval_options.chapter_titles is not None
        else None,
    )
    if not candidates:
        return []

    vector_results = manual_store.query(
        vehicle_id, question, manual_store.collection.count()
    )
    scoped_ids = {chunk.id for chunk in candidates}
    vector_results = [chunk for chunk in vector_results if chunk.id in scoped_ids]
    vector_by_id = {chunk.id: chunk for chunk in vector_results}
    tokenized_candidates = [_tokenize(chunk.text) for chunk in candidates]
    if not any(tokenized_candidates):
        return [
            chunk for chunk in vector_results
            if chunk.distance <= retrieval_options.minimum_distance
        ][: retrieval_options.limit]
    bm25_scores = BM25Okapi(tokenized_candidates).get_scores(_tokenize(question))
    bm25_ranks = {
        candidates[index].id: rank
        for rank, index in enumerate(
            sorted(range(len(candidates)), key=lambda index: -bm25_scores[index]),
            start=1,
        )
    }
    vector_ranks = {
        chunk.id: rank for rank, chunk in enumerate(vector_results, start=1)
    }
    fused = [
        (
            1 / (60 + vector_ranks[chunk.id])
            + 1 / (60 + bm25_ranks[chunk.id]),
            vector_by_id[chunk.id],
        )
        for chunk in candidates
        if chunk.id in vector_by_id
    ]
    return [
        chunk
        for _, chunk in sorted(fused, key=lambda item: (-item[0], item[1].distance))
        if chunk.distance <= retrieval_options.minimum_distance
    ][: retrieval_options.limit]


def retrieve_baseline_evidence(
    vehicle_id: str,
    question: str,
    minimum_distance: float = 1.1,
    *,
    store: ManualStore | None = None,
) -> list[RetrievedChunk]:
    """Return the original vector-only top-four retrieval result."""
    manual_store = store or ManualStore()
    return [
        chunk
        for chunk in manual_store.query(vehicle_id, question, limit=4)
        if chunk.distance <= minimum_distance
    ]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())
