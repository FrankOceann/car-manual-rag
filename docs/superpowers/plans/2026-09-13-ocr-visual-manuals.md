# OCR 与图文汽车手册 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持扫描版及图文混排 PDF 的本地 OCR、页内图片资产和可选视觉描述，并保持车型、手册版本和页码引用可追溯。

**Architecture:** `pypdf` 继续为带文字层的页面提供文本；新增 PyMuPDF 负责渲染扫描页及提取嵌入图片，RapidOCR 在 CPU 本地识别文字。导入任务把 OCR 和图片描述转换为带 `evidence_type` 的既有 `ManualChunk`，以当前 Chroma、版本过滤和引用管线统一检索；视觉模型只是可选的 OpenAI 兼容适配器。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy/Alembic、Celery、PyMuPDF、RapidOCR + ONNX Runtime、Chroma、React/TypeScript、Docker Compose、pytest、Vitest。

**Spec:** `docs/superpowers/specs/2026-09-13-ocr-visual-manuals-design.md`

## Global Constraints

- OCR 必须默认本地运行，不能要求 API Key 或 Tesseract 二进制文件。
- 视觉模型默认关闭；在未配置时，文字型和 OCR 导入必须继续可用。
- 每条图文证据必须包含 `vehicle_id`、`manual_id`、`version_id` 和 `page_number`。
- 原始 PDF 是最终引用来源；视觉描述必须标识为模型生成，不能冒充原文。
- 资料、图片和接口延续登录、管理员权限、活动版本和停用过滤。
- 单次图片/扫描页 OCR 失败不得让已有可用文字的手册导入失败。
- 新增依赖锁定进 `backend/requirements.lock`；不得写入任何密钥、私人手册或模型缓存。

---

## File Structure

| 文件 | 职责 |
|---|---|
| `backend/app/models.py` | 增加 `ManualAsset` 持久化模型。 |
| `backend/migrations/versions/0002_visual_assets.py` | 创建图片资产表。 |
| `backend/app/config.py` | OCR、渲染和视觉模型配置。 |
| `backend/app/rag/visual.py` | 页渲染、图片抽取、OCR、视觉描述适配器及纯数据对象。 |
| `backend/app/rag/chunking.py` | 把 OCR/图片证据转为带来源元数据的 `ManualChunk`。 |
| `backend/app/importing.py` | 扩展任务状态、幂等存储资产、向量化图文证据。 |
| `backend/app/management.py` | 资产预览接口及手册/任务视图。 |
| `backend/app/schemas.py`、`backend/app/rag/answering.py` | 返回和约束图片引用。 |
| `frontend/src/api.ts`、`frontend/src/types.ts`、`frontend/src/components/CitationCard.tsx` | 请求资产和展示图像来源。 |
| `backend/Dockerfile`、`backend/pyproject.toml`、`backend/requirements.lock`、`compose.yaml` | 安装 OCR 依赖、准备模型与持久化配置。 |
| `backend/tests/test_visual.py`、`backend/tests/test_enterprise.py`、`backend/tests/integration/test_services.py` | OCR、导入幂等性、权限与真实依赖链路测试。 |

### Task 1: 建立 OCR 与图片资产的数据边界

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/models.py`
- Create: `backend/migrations/versions/0002_visual_assets.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/tests/test_enterprise.py`

**Interfaces:**
- Produces `Settings.ocr_enabled: bool`、`ocr_language: str`、`ocr_render_dpi: int`、`vision_enabled: bool`、`vision_base_url: str | None`、`vision_api_key: str | None`、`vision_model: str | None`。
- Produces `ManualAsset(id, version_id, sha256, file_path, page_number, asset_index, width, height, ocr_text, visual_description, processing_status, error)` with unique `(version_id, page_number, asset_index, sha256)`.
- Produces `Citation.asset_id: str | None` and `Citation.evidence_type: str` with `pdf_text` as the backward-compatible default.

- [ ] **Step 1: Write failing model/config tests**

```python
def test_manual_asset_is_unique_within_a_version(session):
    version = make_version(session)
    session.add_all([
        ManualAsset(version_id=version.id, page_number=2, asset_index=0, sha256="a" * 64,
                    file_path="assets/a.png", width=20, height=10, processing_status="succeeded"),
        ManualAsset(version_id=version.id, page_number=2, asset_index=0, sha256="a" * 64,
                    file_path="assets/a.png", width=20, height=10, processing_status="succeeded"),
    ])
    with pytest.raises(IntegrityError):
        session.commit()

def test_ocr_defaults_do_not_require_credentials(monkeypatch):
    monkeypatch.delenv("VISION_API_KEY", raising=False)
    settings = Settings()
    assert settings.ocr_enabled is True
    assert settings.ocr_language == "ch"
    assert settings.vision_enabled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_enterprise.py -k "manual_asset or ocr_defaults" -v`  
Expected: FAIL because `ManualAsset` and OCR settings do not exist.

- [ ] **Step 3: Implement the smallest persistent schema and settings**

```python
class ManualAsset(Base):
    __tablename__ = "manual_assets"
    __table_args__ = (UniqueConstraint("version_id", "page_number", "asset_index", "sha256"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("manual_versions.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    file_path: Mapped[str] = mapped_column(Text)
    page_number: Mapped[int] = mapped_column(Integer)
    asset_index: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    visual_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Create Alembic revision `0002_visual_assets` that creates exactly this table and index. Add the six settings with defaults `True`, `"ch"`, `200`, `False`, `None`, `None`, `None`.

- [ ] **Step 4: Run model/config tests to verify they pass**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_enterprise.py -k "manual_asset or ocr_defaults" -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/app/models.py backend/app/schemas.py backend/migrations/versions/0002_visual_assets.py backend/tests/test_enterprise.py
git commit -m "feat: store manual visual assets"
```

### Task 2: 实现可测试的本地 OCR 与 PDF 图片提取

**Files:**
- Create: `backend/app/rag/visual.py`
- Create: `backend/tests/test_visual.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/requirements.lock`

**Interfaces:**
- Produces `ExtractedAsset(page_number: int, asset_index: int, data: bytes, suffix: str, width: int, height: int)`.
- Produces `VisualPage(page_number: int, page_ocr_text: str, assets: list[ExtractedAsset])`.
- Produces `extract_visual_pages(pdf_path: Path, *, ocr: OCRClient, dpi: int, run_page_ocr: Callable[[str], bool]) -> list[VisualPage]`.
- Produces `RapidOCRClient.recognize(image: bytes) -> str`; an empty OCR result is valid.

- [ ] **Step 1: Write failing extraction tests with a fake OCR client**

```python
class FakeOCR:
    def __init__(self, values): self.values = iter(values)
    def recognize(self, image: bytes) -> str: return next(self.values)

def test_scanned_page_is_rendered_and_keeps_one_based_page_number(tmp_path):
    pdf = create_image_only_pdf(tmp_path / "scan.pdf", "轮胎压力")
    pages = extract_visual_pages(pdf, ocr=FakeOCR(["轮胎压力"]), dpi=200,
                                 run_page_ocr=lambda _: True)
    assert pages[0].page_number == 1
    assert pages[0].page_ocr_text == "轮胎压力"

def test_text_page_skips_full_page_ocr_but_extracts_embedded_image(tmp_path):
    pdf = create_text_and_image_pdf(tmp_path / "mixed.pdf")
    pages = extract_visual_pages(pdf, ocr=FakeOCR(["故障灯"]), dpi=200,
                                 run_page_ocr=lambda text: len(text) < 20)
    assert pages[0].page_ocr_text == ""
    assert pages[0].assets[0].page_number == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_visual.py -v`  
Expected: FAIL because `app.rag.visual` is missing.

- [ ] **Step 3: Add dependencies and minimal implementation**

Add `pymupdf` and `rapidocr` plus `onnxruntime` to the project dependency list and regenerate the lock file. Implement image extraction with `pymupdf.open`, `page.get_images(full=True)`, and `document.extract_image(xref)`. Render only pages selected by `run_page_ocr` with `page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")`. `RapidOCRClient` constructs one cached `RapidOCR` instance and joins recognised line text in reading order; it returns `""` when the OCR result is empty.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_visual.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/visual.py backend/tests/test_visual.py backend/pyproject.toml backend/requirements.lock
git commit -m "feat: extract manual images with local OCR"
```

### Task 3: 将 OCR 和图片资产接入可恢复导入任务

**Files:**
- Modify: `backend/app/rag/chunking.py`
- Modify: `backend/app/importing.py`
- Modify: `backend/app/management.py`
- Modify: `backend/tests/test_enterprise.py`
- Modify: `backend/tests/test_chunking.py`

**Interfaces:**
- Produces `chunk_evidence_text(text, *, page_number, vehicle, manual_title, chapter_title, evidence_type, asset_id=None) -> list[ManualChunk]`.
- Produces `persist_visual_assets(session, version, visual_pages, root) -> list[ManualAsset]`.
- Extends import states to allow `ocr` between `parsing` and `embedding`.
- Produces `GET /manuals/{manual_id}/versions/{version_id}/assets/{asset_id}/file`, authenticated and ownership-checked.

- [ ] **Step 1: Write failing chunking and import idempotence tests**

```python
def test_image_ocr_chunk_has_asset_and_version_provenance(vehicle):
    chunks = chunk_evidence_text("胎压警告", page_number=2, vehicle=vehicle,
        manual_title="测试手册", chapter_title="第 2 页", evidence_type="image_ocr", asset_id="asset-1")
    assert chunks[0].metadata["evidence_type"] == "image_ocr"
    assert chunks[0].metadata["asset_id"] == "asset-1"

def test_reprocessing_same_version_does_not_duplicate_assets_or_chunks(monkeypatch, session, version):
    run_import_twice(monkeypatch, version.id)
    assert asset_count(session, version.id) == 1
    assert chroma_chunk_count_for_version(version.id) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_chunking.py tests/test_enterprise.py -k "image_ocr or duplicate_assets" -v`  
Expected: FAIL because evidence metadata and asset persistence do not exist.

- [ ] **Step 3: Implement minimal import integration**

Add `chunk_evidence_text` by delegating to existing `chunk_page_text` and merging `evidence_type` plus optional `asset_id`. In `process_job`, retain existing `pypdf` text chunks, set progress to `ocr`, invoke `extract_visual_pages` only when `ocr_enabled`, SHA-256 each extracted image, and insert-or-reuse its `ManualAsset`. Convert nonempty page OCR and asset OCR into chunks, prefix chunk IDs with the version ID as today, then upsert fixed batches of 64. Do not upsert an image OCR chunk on repeated processing when its deterministic ID already exists. Store visual processing errors on that asset and continue. Add the authenticated file endpoint with `manual_id`, `version_id`, asset ID and resolved storage-root path checks equivalent to `original_pdf`.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_chunking.py tests/test_enterprise.py -k "image_ocr or duplicate_assets" -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/chunking.py backend/app/importing.py backend/app/management.py backend/tests/test_chunking.py backend/tests/test_enterprise.py
git commit -m "feat: import OCR evidence and manual images"
```

### Task 4: 增加可选视觉描述及安全的回答引用

**Files:**
- Modify: `backend/app/rag/visual.py`
- Modify: `backend/app/rag/answering.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/tests/test_answering.py`
- Modify: `backend/tests/test_visual.py`

**Interfaces:**
- Produces `VisualDescriber.describe(image: bytes) -> str`.
- Uses `OpenAI(base_url=settings.vision_base_url, api_key=settings.vision_api_key)` only when `vision_enabled` is true and all three visual configuration values exist.
- Produces `image_description` chunks with metadata `model_generated: "true"` and `asset_id`.
- Extends server-side citation de-duplication key from `(version_id, page_number)` to `(version_id, page_number, asset_id or "")`.

- [ ] **Step 1: Write failing visual configuration and citation tests**

```python
def test_disabled_visual_model_never_constructs_client(monkeypatch):
    monkeypatch.setattr(Settings, "vision_enabled", False)
    assert describe_if_enabled(b"png") is None

def test_same_page_different_images_keep_two_citations(service, vehicle):
    response = service.answer_question("这两个图标是什么", vehicle, [
        chunk("a", page=2, asset="left"), chunk("b", page=2, asset="right"),
    ])
    assert [item.asset_id for item in response.citations] == ["left", "right"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_visual.py tests/test_answering.py -k "disabled_visual or different_images" -v`  
Expected: FAIL because visual adapter and image-specific citation de-duplication do not exist.

- [ ] **Step 3: Implement optional adapter and citation handling**

Validate visual settings before a request: if `VISION_ENABLED=true` but URL, model or key is missing, raise one explicit configuration error for the import job. Encode a PNG as a data URL and request a short neutral description that labels visible symbols, labels and diagram relationships but forbids repair instructions. On remote model failure, set the asset error and continue with OCR evidence. Include `evidence_type` and `asset_id` in the answer prompt but continue accepting only IDs present in retrieved evidence. Update citations to propagate these fields and use the new de-duplication key.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_visual.py tests/test_answering.py -k "disabled_visual or different_images" -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/visual.py backend/app/rag/answering.py backend/app/schemas.py backend/tests/test_visual.py backend/tests/test_answering.py
git commit -m "feat: add optional visual descriptions to citations"
```

### Task 5: 展示图片引用并保持权限边界

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/components/CitationCard.tsx`
- Modify: `frontend/src/Enterprise.test.tsx`
- Modify: `backend/tests/test_enterprise.py`

**Interfaces:**
- Produces `Citation.asset_id?: string` and `Citation.evidence_type: "pdf_text" | "page_ocr" | "image_ocr" | "image_description"`.
- Produces `openAsset(manualId: string, versionId: string, assetId: string, token: string): Promise<Blob>`.
- `CitationCard` calls `onOpenAsset(citation)` only when `asset_id` exists; otherwise it preserves the current PDF action.

- [ ] **Step 1: Write failing UI and authorization tests**

```tsx
it("opens an image asset for image OCR evidence", async () => {
  render(<CitationCard citation={imageCitation} onOpen={vi.fn()} onOpenAsset={openAsset} busy={false} />);
  await userEvent.click(screen.getByRole("button", { name: "查看关联图片" }));
  expect(openAsset).toHaveBeenCalledWith(imageCitation);
});
```

```python
def test_asset_file_requires_authenticated_user(client, seeded_asset):
    response = client.get(asset_url(seeded_asset))
    assert response.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend; npm test -- --run src/Enterprise.test.tsx`  
Expected: FAIL because image citation UI does not exist.

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_enterprise.py -k asset_file -v`  
Expected: FAIL because asset authorization endpoint does not exist.

- [ ] **Step 3: Implement the smallest UI/API extension**

Add `openAsset` with the existing authenticated fetch helper and return a Blob. In `App.tsx`, create an object URL, open it in a new tab, then revoke it after opening; preserve current PDF behavior for ordinary citations. Render an explicit “图片 OCR 证据” or “模型图片描述” label so users distinguish the evidence source. Do not expose any admin action to readers.

- [ ] **Step 4: Run tests and production build**

Run: `cd frontend; npm test -- --run`  
Expected: PASS.

Run: `cd frontend; npm run build`  
Expected: PASS.

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_enterprise.py -k asset_file -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/App.tsx frontend/src/components/CitationCard.tsx frontend/src/Enterprise.test.tsx backend/tests/test_enterprise.py
git commit -m "feat: display authenticated image citations"
```

### Task 6: 固化 Docker 部署、离线模型准备与端到端验证

**Files:**
- Modify: `backend/Dockerfile`
- Modify: `backend/scripts/prepare_model.py`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Modify: `backend/.env.example`
- Modify: `docs/enterprise-deployment.md`
- Modify: `backend/tests/integration/test_services.py`
- Modify: `.github/workflows/ci.yaml`

**Interfaces:**
- `python -m scripts.prepare_model` prepares embedding and OCR resources before offline API/worker startup.
- Compose persists OCR resources alongside the existing `models` volume and passes all OCR/vision environment values only to API and worker.
- Integration test creates a self-authored text page plus image-only page and asserts an authenticated OCR citation for page two.

- [ ] **Step 1: Write failing Docker/static and integration assertions**

```python
def test_dockerfile_installs_pymupdf_and_rapidocr():
    dockerfile = Path("backend/Dockerfile").read_text(encoding="utf-8")
    assert "pymupdf" in dockerfile.lower() or "requirements.lock" in dockerfile
    assert "rapidocr" in Path("backend/requirements.lock").read_text(encoding="utf-8").lower()

def test_imported_scanned_page_is_retrievable_from_services(compose_services):
    response = upload_self_authored_scanned_pdf(compose_services)
    assert wait_for_job(response["job_id"])["status"] == "succeeded"
    assert retrieved_pages("扫描页轮胎警告") == [2]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/integration/test_services.py -k scanned -v`  
Expected: FAIL because the fixture and OCR pipeline are absent.

- [ ] **Step 3: Implement deployment support**

Install locked OCR dependencies in the existing backend Docker image and update `prepare_model.py` to instantiate `RapidOCR` during the setup profile, failing clearly if the OCR resource is unavailable. Add all environment names to examples with safe defaults and blank vision credentials. Update Compose model volume usage and the docs with `docker compose --profile setup run --rm prepare-model`, CPU performance expectations, and the rule that visual descriptions are optional. Extend CI with unit tests and a mocked visual adapter; leave real OCR integration as an opt-in compose test so CI does not require a private PDF or paid provider.

- [ ] **Step 4: Run full verification**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -q`  
Expected: PASS.

Run: `cd frontend; npm test -- --run; npm run build`  
Expected: PASS.

Run: `python deployment/test_backup.py; python deployment/test_dockerfile.py`  
Expected: PASS.

Run: `docker compose --profile setup run --rm prepare-model; docker compose up -d --build; docker compose ps`  
Expected: `postgres`、`redis`、`chroma`、`api`、`worker`、`beat`、`nginx` healthy or running as appropriate.

- [ ] **Step 5: Commit**

```bash
git add backend/Dockerfile backend/scripts/prepare_model.py compose.yaml .env.example backend/.env.example docs/enterprise-deployment.md backend/tests/integration/test_services.py .github/workflows/ci.yaml
git commit -m "feat: deploy local OCR for manual imports"
```

## Plan Self-Review

- Spec coverage: Tasks 1–3 implement persistent assets, OCR, page/image evidence, idempotent import and authenticated original access; Task 4 implements optional visual description and citation integrity; Task 5 implements reader-facing asset use; Task 6 implements configuration, offline preparation, CI and deployment verification.
- 占位内容检查：计划中的每个任务均给出了具体文件、接口、失败测试、验证命令和提交范围。
- Type consistency: `ManualAsset`, `ExtractedAsset`, `VisualPage`, `chunk_evidence_text`, `asset_id`, `evidence_type` and settings names are defined before later tasks consume them.
