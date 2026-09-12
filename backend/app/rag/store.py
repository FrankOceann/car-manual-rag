from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from functools import lru_cache
from threading import Lock
import atexit

from app.config import Settings
from app.rag.chunking import ManualChunk


class EmbeddingModel(Protocol):
    def encode(self, values: list[str]) -> Any: ...


class VectorCollection(Protocol):
    def count(self) -> int: ...

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

    def get(
        self,
        *,
        where: dict[str, str],
        include: list[str],
    ) -> dict[str, list[Any]]: ...

    def delete(self, *, ids: list[str], where: dict[str, str]) -> None: ...


@dataclass(frozen=True)
class RetrievedChunk:
    id: str
    text: str
    metadata: dict[str, str | int]
    distance: float


class ManualStore:
    """Local Chroma-backed manual storage with mandatory vehicle filtering."""

    MODEL_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "models"

    def __init__(
        self,
        chroma_path: str | Path | None = None,
        *,
        collection: VectorCollection | None = None,
        embedding_model: EmbeddingModel | None = None,
        version_ids: set[str] | None = None,
    ):
        self.collection = collection or self._create_collection(chroma_path)
        self.embedding_model = embedding_model
        self.version_ids = version_ids

    @staticmethod
    def _create_collection(chroma_path: str | Path | None) -> VectorCollection:
        import chromadb

        settings = Settings()
        path = Path(chroma_path or settings.chroma_path)
        client = (_shared_http_client(settings.chroma_host, settings.chroma_port)
                  if settings.chroma_host and chroma_path is None
                  else chromadb.PersistentClient(path=str(path)))
        return client.get_or_create_collection(
            name="manual_chunks", metadata={"hnsw:space": "cosine"}
        )

    @classmethod
    def _create_embedding_model(cls) -> EmbeddingModel:
        with _model_lock:
            return _load_model(Settings().model_path, str(cls.MODEL_CACHE_DIR))

    def where(self, vehicle_id: str):
        if self.version_ids is None:
            return {"vehicle_id": vehicle_id}
        return {"$and": [{"vehicle_id": vehicle_id},
                         {"version_id": {"$in": sorted(self.version_ids) or ["__no_active_version__"]}}]}

    def _visible(self, metadata):
        return self.version_ids is None or metadata.get("version_id") in self.version_ids

    def upsert(self, chunks: list[ManualChunk]) -> None:
        if not chunks:
            return
        self.collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            metadatas=[chunk.metadata for chunk in chunks],
            embeddings=self._embedding_model().encode([chunk.text for chunk in chunks]),
        )

    def reconcile_version(self, version_id: str, chunk_ids: set[str]) -> None:
        """Remove evidence omitted by a successful retry, confined to this version."""
        result = self.collection.get(where={"version_id": version_id}, include=[])
        stale_ids = [item_id for item_id in result["ids"] if item_id not in chunk_ids]
        for offset in range(0, len(stale_ids), 64):
            self.collection.delete(ids=stale_ids[offset:offset + 64], where={"version_id": version_id})

    def list_chunks(
        self, vehicle_id: str, chapter_titles: set[str] | None = None
    ) -> list[RetrievedChunk]:
        if not vehicle_id:
            raise ValueError("vehicle_id is required")
        if chapter_titles == set() or self.version_ids == set() or self.collection.count() == 0:
            return []

        result = self.collection.get(
            where=self.where(vehicle_id),
            include=["documents", "metadatas"],
        )
        ids = result.get("ids", []) or []
        documents = result.get("documents", []) or []
        metadatas = result.get("metadatas", []) or []
        return [
            RetrievedChunk(id=item_id, text=text, metadata=metadata, distance=0.0)
            for item_id, text, metadata in zip(ids, documents, metadatas)
            if metadata.get("vehicle_id") == vehicle_id
            and self._visible(metadata)
            and (
                chapter_titles is None
                or metadata.get("chapter_title") in chapter_titles
            )
        ]

    def query(self, vehicle_id: str, question: str, limit: int = 4) -> list[RetrievedChunk]:
        if not vehicle_id:
            raise ValueError("vehicle_id is required")
        if limit <= 0 or self.version_ids == set():
            return []
        if self.collection.count() == 0:
            return []

        result = self.collection.query(
            query_embeddings=self._embedding_model().encode([question]),
            n_results=limit,
            where=self.where(vehicle_id),
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
            and self._visible(metadata)
        ]

    def _embedding_model(self) -> EmbeddingModel:
        if self.embedding_model is None:
            self.embedding_model = self._create_embedding_model()
        return self.embedding_model


_model_lock = Lock()
_client_lock = Lock()
_http_clients: dict[tuple[str, int], Any] = {}


def _shared_http_client(host: str, port: int):
    import chromadb
    with _client_lock:
        key = (host, port)
        if key not in _http_clients:
            _http_clients[key] = chromadb.HttpClient(host=host, port=port)
        return _http_clients[key]


def close_shared_clients():
    with _client_lock:
        for client in _http_clients.values():
            client.close()
        _http_clients.clear()


atexit.register(close_shared_clients)


@lru_cache(maxsize=2)
def _load_model(model_path: str, cache_path: str):
    from sentence_transformers import SentenceTransformer
    prepared_model = Path(model_path)
    if Settings().chroma_host and (prepared_model / "modules.json").is_file():
        return SentenceTransformer(model_path, device="cpu", local_files_only=True)
    # Existing developer cache remains usable offline; hosted mode must be prepared explicitly.
    if Settings().chroma_host:
        raise RuntimeError("Embedding model is not prepared. Run scripts.prepare_model.")
    Path(cache_path).mkdir(parents=True, exist_ok=True)
    return SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                               cache_folder=cache_path, device="cpu", local_files_only=True)
