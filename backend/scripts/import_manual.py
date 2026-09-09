from __future__ import annotations

import argparse
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.catalog import get_vehicle
from app.rag.chunking import extract_chunks
from app.rag.store import ManualStore


def provenance_is_confirmed(vehicle_id: str, sources_path: Path) -> bool:
    """Return whether the selected vehicle's source table row is usable."""
    vehicle = get_vehicle(vehicle_id)
    if vehicle is None:
        return False

    vehicle_name = f"{vehicle.brand}{vehicle.model}"
    covered_model = f"{vehicle_name} {vehicle.year}"
    for line in sources_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 7 and cells[0] == vehicle_name and cells[1] == covered_model:
            return cells[6] == "已确认可用"
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a confirmed vehicle manual into local Chroma storage.")
    parser.add_argument("--vehicle-id", required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()

    vehicle = get_vehicle(args.vehicle_id)
    if vehicle is None:
        parser.error(f"Unsupported vehicle_id: {args.vehicle_id}")
    if not args.pdf.is_file():
        parser.error(f"PDF file does not exist: {args.pdf}")

    sources_path = BACKEND_ROOT.parent / "docs" / "data-sources.md"
    if not provenance_is_confirmed(args.vehicle_id, sources_path):
        parser.error("Manual provenance is not marked 已确认可用; import aborted.")

    chunks = extract_chunks(args.pdf, vehicle, args.title)
    ManualStore().upsert(chunks)
    from pypdf import PdfReader

    page_count = len(PdfReader(args.pdf).pages)
    print(f"Imported {len(chunks)} chunks from {page_count} pages for {vehicle.id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
