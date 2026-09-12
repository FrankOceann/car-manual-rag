from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Protocol

import pymupdf
from rapidocr import RapidOCR


class OCRClient(Protocol):
    def recognize(self, image: bytes) -> str: ...


@dataclass(frozen=True, slots=True)
class ExtractedAsset:
    page_number: int
    asset_index: int
    data: bytes
    suffix: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class VisualPage:
    page_number: int
    page_ocr_text: str
    assets: list[ExtractedAsset]


class RapidOCRClient:
    @cached_property
    def _engine(self) -> RapidOCR:
        return RapidOCR()

    def recognize(self, image: bytes) -> str:
        result = self._engine(image)
        if result is None or not result.txts:
            return ""
        return "\n".join(result.txts)


def extract_visual_pages(
    pdf_path: Path,
    *,
    ocr: OCRClient,
    dpi: int,
    run_page_ocr: Callable[[str], bool],
) -> list[VisualPage]:
    visual_pages: list[VisualPage] = []

    with pymupdf.open(pdf_path) as document:
        for page_index, page in enumerate(document):
            page_number = page_index + 1
            assets = [
                _extract_asset(document, image, page_number, asset_index)
                for asset_index, image in enumerate(page.get_images(full=True))
            ]
            page_text = page.get_text("text")
            page_ocr_text = ""
            if run_page_ocr(page_text):
                rendered_page = page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")
                page_ocr_text = ocr.recognize(rendered_page)

            visual_pages.append(
                VisualPage(
                    page_number=page_number,
                    page_ocr_text=page_ocr_text,
                    assets=assets,
                )
            )

    return visual_pages


def _extract_asset(
    document: pymupdf.Document,
    image: tuple,
    page_number: int,
    asset_index: int,
) -> ExtractedAsset:
    extracted = document.extract_image(image[0])
    return ExtractedAsset(
        page_number=page_number,
        asset_index=asset_index,
        data=extracted["image"],
        suffix=extracted["ext"],
        width=extracted["width"],
        height=extracted["height"],
    )
