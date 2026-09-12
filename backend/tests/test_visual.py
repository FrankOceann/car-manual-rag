from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import base64
import pytest
from app.config import Settings
from app.rag import visual
from tests.test_enterprise import system, upload_image_pdf, ImportStoreStub

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


def vision_settings(**changes):
    values = dict(_env_file=None, vision_enabled=True, vision_base_url="https://vision.invalid/v1",
                  vision_api_key="test-only-key", vision_model="test-vision")
    return Settings(**(values | changes))


def fake_vision_client(monkeypatch, *, description=" 圆形警告符号与标签相连。 ", failure=None):
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        if failure:
            raise failure
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=description))])
    def factory(**kwargs):
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(visual, "OpenAI", factory, raising=False)
    return requests


def test_disabled_vision_never_constructs_client(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("Disabled vision constructed a remote client")
    monkeypatch.setattr(visual, "OpenAI", forbidden, raising=False)
    describer = visual.VisualDescriber(vision_settings(vision_enabled=False))
    assert describer.describe(_png_bytes()) == ""


@pytest.mark.parametrize("missing", ["vision_base_url", "vision_api_key", "vision_model"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_enabled_vision_rejects_incomplete_configuration(monkeypatch, missing, value):
    def forbidden(**kwargs):
        pytest.fail("Incomplete configuration constructed a remote client")
    monkeypatch.setattr(visual, "OpenAI", forbidden, raising=False)
    with pytest.raises(ValueError, match="VISION_"):
        visual.VisualDescriber(vision_settings(**{missing: value}))


def test_visual_description_sends_image_and_neutral_constraints(monkeypatch):
    requests = fake_vision_client(monkeypatch)
    describer = visual.VisualDescriber(vision_settings())
    assert describer.describe(_png_bytes()) == "圆形警告符号与标签相连。"
    request = requests[0]
    assert request["model"] == "test-vision"
    content = request["messages"][1]["content"]
    image_url = next(item["image_url"]["url"] for item in content if item["type"] == "image_url")
    assert image_url.startswith("data:image/png;base64,")
    from PIL import Image
    with Image.open(BytesIO(base64.b64decode(image_url.split(",", 1)[1]))) as image:
        assert image.size == (32, 24)
    prompt = request["messages"][0]["content"]
    for term in ["维修", "扭矩", "零件号", "编造", "符号", "标签", "关系"]:
        assert term in prompt


@pytest.mark.parametrize("description", [None, "", "   "])
def test_empty_visual_response_does_not_invent_description(monkeypatch, description):
    fake_vision_client(monkeypatch, description=description)
    assert visual.VisualDescriber(vision_settings()).describe(_png_bytes()) == ""


@pytest.fixture
def vision_import(system, monkeypatch):
    client, admin, _ = system
    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_BASE_URL", "https://vision.invalid/v1")
    monkeypatch.setenv("VISION_API_KEY", "test-only-key")
    monkeypatch.setenv("VISION_MODEL", "test-vision")
    monkeypatch.setattr(RapidOCRClient, "recognize", lambda self, data: "Original OCR label")
    chunks = []
    class Store(ImportStoreStub):
        def upsert(self, values):
            chunks.extend(values)
    monkeypatch.setattr("app.importing.ManualStore", Store)
    return client, admin, chunks


def test_import_incomplete_vision_configuration_fails_clearly(vision_import, monkeypatch):
    from app.importing import process_job
    client, admin, chunks = vision_import
    fake_vision_client(monkeypatch)
    monkeypatch.setenv("VISION_MODEL", "")
    data = upload_image_pdf(client, admin)
    process_job(data["job_id"])
    job = client.get("/jobs/" + data["job_id"], headers=admin).json()
    assert job["status"] == "failed"
    assert "VISION_MODEL" in job["error"]
    assert chunks == []


@pytest.mark.parametrize("ocr_enabled", [True, False])
def test_import_persists_model_description_with_asset_provenance(vision_import, monkeypatch, ocr_enabled):
    from sqlalchemy import select
    from app.db import session_factory
    from app.importing import process_job
    from app.models import ManualAsset
    client, admin, chunks = vision_import
    monkeypatch.setenv("OCR_ENABLED", str(ocr_enabled).lower())
    fake_vision_client(monkeypatch)
    data = upload_image_pdf(client, admin)
    process_job(data["job_id"])
    descriptions = [c for c in chunks if c.metadata.get("evidence_type") == "image_description"]
    assert len(descriptions) == 1
    chunk = descriptions[0]
    assert chunk.text == "圆形警告符号与标签相连。"
    assert chunk.metadata["model_generated"] == "true"
    assert chunk.metadata["manual_id"] == data["manual_id"]
    assert chunk.metadata["version_id"] == data["version_id"]
    assert chunk.metadata["page_number"] == 1
    with session_factory()() as session:
        asset = session.scalar(select(ManualAsset))
        assert asset.visual_description == chunk.text
        assert asset.id == chunk.metadata["asset_id"]
        assert asset.ocr_text == ("Original OCR label" if ocr_enabled else "")
    assert client.get("/jobs/" + data["job_id"], headers=admin).json()["status"] == "succeeded"


def test_remote_vision_failure_keeps_ocr_and_records_safe_asset_error(vision_import, monkeypatch):
    from sqlalchemy import select
    from app.db import session_factory
    from app.importing import process_job
    from app.models import ManualAsset
    client, admin, chunks = vision_import
    fake_vision_client(monkeypatch, failure=RuntimeError("private remote key details"))
    data = upload_image_pdf(client, admin, text="")
    process_job(data["job_id"])
    assert client.get("/jobs/" + data["job_id"], headers=admin).json()["status"] == "succeeded"
    assert {c.metadata["evidence_type"] for c in chunks} == {"image_ocr", "page_ocr"}
    with session_factory()() as session:
        asset = session.scalar(select(ManualAsset))
        assert asset.ocr_text == "Original OCR label"
        assert asset.visual_description is None
        assert asset.processing_status == "failed"
        assert asset.error and "视觉" in asset.error
        assert "private" not in asset.error
