from app.catalog import get_vehicle
import sys
from types import SimpleNamespace

from app.rag.chunking import chunk_page_text, extract_chunks


def test_chunk_page_text_preserves_vehicle_and_page_metadata():
    vehicle = get_vehicle("toyota-corolla")
    assert vehicle is not None

    chunks = chunk_page_text(
        page_text="轮胎压力不足时，请立即检查轮胎。" * 80,
        page_number=42,
        vehicle=vehicle,
        manual_title="卡罗拉用户手册",
        chapter_title="轮胎",
    )

    assert chunks[0].id == "toyota-corolla-p42-c0"
    assert chunks[0].metadata == {
        "vehicle_id": "toyota-corolla",
        "brand": "丰田",
        "model": "卡罗拉",
        "year": 2024,
        "manual_title": "卡罗拉用户手册",
        "chapter_title": "轮胎",
        "page_number": 42,
        "source_text": chunks[0].text,
    }


def test_chunk_page_text_normalizes_whitespace_and_uses_overlapping_windows():
    vehicle = get_vehicle("byd-seal")
    assert vehicle is not None

    chunks = chunk_page_text(
        page_text="  alpha\n\n beta\t gamma  ",
        page_number=3,
        vehicle=vehicle,
        manual_title="海豹用户手册",
        chapter_title="第 3 页",
        chunk_size=10,
        overlap=3,
    )

    assert [chunk.id for chunk in chunks] == ["byd-seal-p3-c0", "byd-seal-p3-c1"]
    assert [chunk.text for chunk in chunks] == ["alpha beta", "eta gamma"]
    assert [chunk.metadata["source_text"] for chunk in chunks] == ["alpha beta", "eta gamma"]


def test_chunk_page_text_skips_blank_pages():
    vehicle = get_vehicle("honda-civic")
    assert vehicle is not None

    assert chunk_page_text(
        page_text=" \n\t ",
        page_number=1,
        vehicle=vehicle,
        manual_title="思域用户手册",
        chapter_title="第 1 页",
    ) == []


def test_extract_chunks_uses_pdf_pages_and_default_page_chapter_titles(monkeypatch, tmp_path):
    vehicle = get_vehicle("toyota-corolla")
    assert vehicle is not None
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.touch()

    class FakeReader:
        pages = [
            SimpleNamespace(extract_text=lambda: "第一页内容"),
            SimpleNamespace(extract_text=lambda: "第二页内容"),
        ]

        def __init__(self, path):
            assert path == pdf_path

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakeReader))

    chunks = extract_chunks(pdf_path, vehicle, "卡罗拉用户手册")

    assert [(chunk.id, chunk.text, chunk.metadata["chapter_title"], chunk.metadata["page_number"])
            for chunk in chunks] == [
        ("toyota-corolla-p1-c0", "第一页内容", "第 1 页", 1),
        ("toyota-corolla-p2-c0", "第二页内容", "第 2 页", 2),
    ]
