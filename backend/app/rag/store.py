from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings
from app.rag.chunking import ManualChunk


class EmbeddingModel(Protocol):
    def encode(self, values: list[str]) -> Any: ...


class VectorCollection(Protocol):
    def upsert(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, str | int]],
        embeddings: Any,
    ) -> None: ...

    def query(
        self,
        *,
        query_embeddings: Any,
        n_results: int,
        where: dict[str, str],
        include: list[str],
    ) -> dict[str, list[list[Any]]]: ...


@dataclass(frozen=True)
class RetrievedChunk:
    id: str
    text: str
    metadata: dict[str, str | int]
    distance: float


class ManualStore:
    """Local Chroma-backed manual storage with mandatory vehicle filtering."""

    def __init__(
        self,
        chroma_path: str | Path | None = None,
        *,
        collection: VectorCollection | None = None,
        embedding_model: EmbeddingModel | None = None,
    ):
        self.collection = collection or self._create_collection(chroma_path)
        self.embedding_model = embedding_model

    @staticmethod
    def _create_collection(chroma_path: str | Path | None) -> VectorCollection:
        import chromadb

        path = Path(chroma_path or Settings().chroma_path)
        client = chromadb.PersistentClient(path=str(path))
        return client.get_or_create_collection(
            name="manual_chunks", metadata={"hnsw:space": "cosine"}
        )

    @staticmethod
    def _create_embedding_model() -> EmbeddingModel:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

    def upsert(self, chunks: list[ManualChunk]) -> None:
        if not chunks:
            return
        self.collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            metadatas=[chunk.metadata for chunk in chunks],
            embeddings=self._embedding_model().encode([chunk.text for chunk in chunks]),
        )

    def query(self, vehicle_id: str, question: str, limit: int = 4) -> list[RetrievedChunk]:
        if not vehicle_id:
            raise ValueError("vehicle_id is required")
        if limit <= 0:
            return []

        result = self.collection.query(
            query_embeddings=self._embedding_model().encode([question]),
            n_results=limit,
            where={"vehicle_id": vehicle_id},
            include=["documents", "metadatas", "distances"],
        )
        documents = result.get("documents", [[]])[0] or []
        metadatas = result.get("metadatas", [[]])[0] or []
        distances = result.get("distances", [[]])[0] or []
        ids = result.get("ids", [[]])[0] or []
        return [
            RetrievedChunk(id=item_id, text=text, metadata=metadata, distance=float(distance))
            for item_id, text, metadata, distance in zip(ids, documents, metadatas, distances)
            if metadata.get("vehicle_id") == vehicle_id
        ]

    def _embedding_model(self) -> EmbeddingModel:
        if self.embedding_model is None:
            self.embedding_model = self._create_embedding_model()
        return self.embedding_model
