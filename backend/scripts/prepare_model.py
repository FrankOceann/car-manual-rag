"""Explicitly download/cache the embedding model before serving requests."""
from pathlib import Path
from app.config import Settings


def main():
    from sentence_transformers import SentenceTransformer
    from app.rag.store import ManualStore
    path = Path(Settings().model_path)
    if (path / "modules.json").is_file():
        model = SentenceTransformer(str(path), device="cpu", local_files_only=True)
    else:
        model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                                   device="cpu", cache_folder=str(ManualStore.MODEL_CACHE_DIR))
        path.mkdir(parents=True, exist_ok=True)
        model.save(str(path))
    assert model.encode(["readiness check"]).shape[0] == 1
    print("Embedding model prepared.")


if __name__ == "__main__":
    main()
