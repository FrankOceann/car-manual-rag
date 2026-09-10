"""Compare local retrieval modes without a hosted model or network download."""
import argparse
import os
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings
from app.rag.evaluation import evaluate_cases, load_cases, write_reports
from app.rag.store import ManualStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate local retrieval without calling DeepSeek.")
    parser.add_argument("--mode", choices=["baseline", "hybrid"], required=True)
    parser.add_argument("--cases", type=Path, default=BACKEND_ROOT / "evals" / "cases.yaml")
    args = parser.parse_args(argv)
    # Set before the lazy sentence-transformers/Chroma imports; require cached model files.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    original_cwd = Path.cwd()
    try:
        cases = load_cases(args.cases)
        settings = Settings(_env_file=BACKEND_ROOT / ".env")
        chroma_path = Path(settings.chroma_path)
        # Windows HNSW cannot open this workspace's Unicode absolute path.
        # Keep configured relative paths relative to backend for the whole query.
        os.chdir(BACKEND_ROOT)
        if not (chroma_path / "chroma.sqlite3").is_file():
            raise ValueError(f"Case {cases[0].id}: local Chroma database not found: {chroma_path}")
        report = evaluate_cases(cases, args.mode, ManualStore(chroma_path=chroma_path))
        for path in write_reports(report):
            print(path)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Evaluation error: {exc}\n")
    finally:
        os.chdir(original_cwd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
