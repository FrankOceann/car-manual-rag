from dataclasses import dataclass
from pathlib import Path
import re

from app.schemas import Vehicle


@dataclass(frozen=True)
class ManualChunk:
    id: str
    text: str
    metadata: dict[str, str | int]


def chunk_page_text(
    page_text: str | None,
    page_number: int,
    vehicle: Vehicle,
    manual_title: str,
    chapter_title: str,
    chunk_size: int = 900,
    overlap: int = 120,
) -> list[ManualChunk]:
    """Split one PDF page into overlapping chunks without losing page provenance."""
    normalized_text = re.sub(r"\s+", " ", page_text or "").strip()
    if not normalized_text:
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be at least zero and smaller than chunk_size")

    metadata = {
        "vehicle_id": vehicle.id,
        "brand": vehicle.brand,
        "model": vehicle.model,
        "year": vehicle.year,
        "manual_title": manual_title,
        "chapter_title": chapter_title,
        "page_number": page_number,
    }
    step = chunk_size - overlap
    chunks = []
    for index, start in enumerate(range(0, len(normalized_text), step)):
        text = normalized_text[start : start + chunk_size]
        if not text:
            break
        chunks.append(
            ManualChunk(
                id=f"{vehicle.id}-p{page_number}-c{index}",
                text=text,
                metadata=metadata.copy(),
            )
        )
        if start + chunk_size >= len(normalized_text):
            break
    return chunks


def extract_chunks(
    pdf_path: Path,
    vehicle: Vehicle,
    manual_title: str,
    chunk_size: int = 900,
    overlap: int = 120,
) -> list[ManualChunk]:
    """Extract page-preserving chunks from a vehicle manual PDF."""
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    chunks: list[ManualChunk] = []
    for page_number, page in enumerate(reader.pages, start=1):
        chunks.extend(
            chunk_page_text(
                page_text=page.extract_text(),
                page_number=page_number,
                vehicle=vehicle,
                manual_title=manual_title,
                chapter_title=f"第 {page_number} 页",
                chunk_size=chunk_size,
                overlap=overlap,
            )
        )
    return chunks
