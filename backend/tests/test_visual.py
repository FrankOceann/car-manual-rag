from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from app.rag.visual import RapidOCRClient, extract_visual_pages


class FakeOCR:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)

    def recognize(self, image: bytes) -> str:
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
        return next(self.values)


def _png_bytes(*, width: int = 32, height: int = 24) -> bytes:
    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (width, height), color="white").save(output, format="PNG")
    return output.getvalue()


def create_image_only_pdf(path, label: str):
    import pymupdf

    document = pymupdf.open()
    page = document.new_page(width=320, height=240)
    page.insert_image(pymupdf.Rect(20, 20, 300, 220), stream=_png_bytes())
    document.set_metadata({"subject": label})
    document.save(path)
    document.close()
    return path


def create_text_and_image_pdf(path):
    import pymupdf

    document = pymupdf.open()
    page = document.new_page(width=320, height=240)
    page.insert_text((20, 30), "This page already has enough searchable text for retrieval.")
    page.insert_image(pymupdf.Rect(20, 50, 300, 220), stream=_png_bytes())
    document.save(path)
    document.close()
    return path


def test_scanned_page_is_rendered_and_keeps_one_based_page_number(tmp_path):
    pdf = create_image_only_pdf(tmp_path / "scan.pdf", "轮胎压力")

    pages = extract_visual_pages(
        pdf,
        ocr=FakeOCR(["轮胎压力"]),
        dpi=200,
        run_page_ocr=lambda _: True,
    )

    assert pages[0].page_number == 1
    assert pages[0].page_ocr_text == "轮胎压力"


def test_text_page_skips_full_page_ocr_but_extracts_embedded_image(tmp_path):
    pdf = create_text_and_image_pdf(tmp_path / "mixed.pdf")

    pages = extract_visual_pages(
        pdf,
        ocr=FakeOCR(["故障灯"]),
        dpi=200,
        run_page_ocr=lambda text: len(text) < 20,
    )

    assert pages[0].page_ocr_text == ""
    assert pages[0].assets[0].page_number == 1
    assert pages[0].assets[0].asset_index == 0
    assert pages[0].assets[0].suffix == "png"
    assert (pages[0].assets[0].width, pages[0].assets[0].height) == (32, 24)


def test_rapidocr_client_joins_recognized_lines_and_reuses_engine(monkeypatch):
    created = []

    class FakeEngine:
        def __init__(self):
            created.append(self)

        def __call__(self, image: bytes):
            assert image == b"image"
            return SimpleNamespace(txts=["第一行", "第二行"])

    monkeypatch.setattr("app.rag.visual.RapidOCR", FakeEngine)
    client = RapidOCRClient()

    assert client.recognize(b"image") == "第一行\n第二行"
    assert client.recognize(b"image") == "第一行\n第二行"
    assert len(created) == 1


def test_rapidocr_client_accepts_empty_result(monkeypatch):
    class FakeEngine:
        def __call__(self, image: bytes):
            return SimpleNamespace(txts=None)

    monkeypatch.setattr("app.rag.visual.RapidOCR", FakeEngine)

    assert RapidOCRClient().recognize(b"blank") == ""
