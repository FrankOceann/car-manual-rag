# 汽车维修手册 RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Web app that answers questions about three selected car models only when their indexed manuals provide supporting evidence and page citations.

**Architecture:** A React/Vite client calls a FastAPI service. The service validates the selected vehicle, filters ChromaDB retrieval by vehicle metadata, and asks DeepSeek to format an answer from retrieved excerpts only. A separate import command extracts PDF pages, creates chunks with page metadata, and persists vectors to ChromaDB.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, ChromaDB, sentence-transformers, pypdf, OpenAI Python SDK (DeepSeek-compatible endpoint), pytest; React 18, TypeScript, Vite, Vitest, Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-09-car-manual-rag-design.md`

## Global Constraints

- Create the new app only under `car-manual-rag/`; do not modify the unrelated `resolveflow/` project.
- Support only the three initial vehicles: BYD Seal, Toyota Corolla, and Honda Civic.
- Every indexed chunk must include brand, model, year, manual title, chapter title, page number, and source text.
- Every grounded response must contain at least one citation with chapter title and page number.
- Missing vehicle selection, unsupported vehicles, insufficient evidence, and dangerous requests without evidence must return `grounded: false` and no invented repair steps.
- Keep `DEEPSEEK_API_KEY` only in `backend/.env`; commit only `backend/.env.example`.
- Record manual provenance and copyright status in `car-manual-rag/docs/data-sources.md` before any manual is imported.

---

## Planned File Structure

```text
car-manual-rag/
├─ .gitignore
├─ README.md
├─ backend/
│  ├─ pyproject.toml
│  ├─ .env.example
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ config.py
│  │  ├─ catalog.py
│  │  ├─ schemas.py
│  │  ├─ api/routes.py
│  │  └─ rag/{chunking.py,store.py,retriever.py,answering.py}
│  ├─ scripts/import_manual.py
│  └─ tests/{test_catalog.py,test_chunking.py,test_retriever.py,test_answering.py,test_routes.py}
├─ frontend/
│  ├─ package.json
│  ├─ src/{main.tsx,App.tsx,api.ts,types.ts,styles.css}
│  └─ src/components/{VehicleSelector.tsx,ChatPanel.tsx,AnswerCard.tsx,CitationCard.tsx}
│  └─ src/**/*.test.tsx
└─ docs/{data-sources.md,test-questions.md}
```

### Task 1: Create the application skeleton and vehicle catalog

**Files:**
- Create: `car-manual-rag/backend/pyproject.toml`
- Create: `car-manual-rag/backend/.env.example`
- Create: `car-manual-rag/.gitignore`
- Create: `car-manual-rag/backend/app/__init__.py`
- Create: `car-manual-rag/backend/app/config.py`
- Create: `car-manual-rag/backend/app/catalog.py`
- Create: `car-manual-rag/backend/app/schemas.py`
- Create: `car-manual-rag/backend/tests/test_catalog.py`
- Create: `car-manual-rag/docs/data-sources.md`
- Create: `car-manual-rag/README.md`

**Interfaces:**
- Produces `Vehicle(id: str, brand: str, model: str, year: int)` and `SUPPORTED_VEHICLES: tuple[Vehicle, ...]`.
- Produces `get_vehicle(vehicle_id: str) -> Vehicle | None`.
- Produces `Settings(deepseek_api_key: str | None, deepseek_model: str, chroma_path: str)`.

- [ ] **Step 1: Write the failing catalog test**

```python
from app.catalog import get_vehicle, list_vehicles

def test_catalog_contains_exactly_three_supported_models():
    assert [(item.brand, item.model) for item in list_vehicles()] == [
        ("比亚迪", "海豹"), ("丰田", "卡罗拉"), ("本田", "思域"),
    ]
    assert get_vehicle("toyota-corolla") is not None
    assert get_vehicle("unknown") is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd car-manual-rag/backend && uv run pytest tests/test_catalog.py -v`

Expected: FAIL because the `app` package does not exist.

- [ ] **Step 3: Initialize project version control and add minimal dependencies**

Run: `git init car-manual-rag`

Create `.gitignore` containing `backend/.env`, `backend/data/chroma/`, `backend/data/manuals/*.pdf`, `__pycache__/`, `.pytest_cache/`, `frontend/node_modules/`, and `frontend/dist/`. Keep the `.gitkeep` file but never commit manuals or local vector data.

Create `pyproject.toml` with runtime dependencies `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `chromadb`, `sentence-transformers`, `pypdf`, and `openai`; add dev dependencies `pytest`, `httpx`, and `pytest-mock`. Implement `Settings` with `deepseek_model="deepseek-chat"`, `deepseek_base_url="https://api.deepseek.com"`, and a local `chroma_path="data/chroma"` default. Create `.env.example` with blank `DEEPSEEK_API_KEY` and the two non-secret settings.

- [ ] **Step 4: Implement the catalog**

```python
SUPPORTED_VEHICLES = (
    Vehicle(id="byd-seal", brand="比亚迪", model="海豹", year=2024),
    Vehicle(id="toyota-corolla", brand="丰田", model="卡罗拉", year=2024),
    Vehicle(id="honda-civic", brand="本田", model="思域", year=2024),
)

def get_vehicle(vehicle_id: str) -> Vehicle | None:
    return next((v for v in SUPPORTED_VEHICLES if v.id == vehicle_id), None)
```

Document each future manual's publisher URL, retrieval date, covered model/year, license or access status, and local filename in `docs/data-sources.md`. The initial rows must be present with status `待确认，不可导入`.

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd car-manual-rag/backend && uv run pytest tests/test_catalog.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add car-manual-rag/backend car-manual-rag/docs/data-sources.md car-manual-rag/README.md
git commit -m "feat: initialize car manual RAG catalog"
```

### Task 2: Implement page-preserving PDF chunking

**Files:**
- Create: `car-manual-rag/backend/app/rag/chunking.py`
- Create: `car-manual-rag/backend/app/rag/__init__.py`
- Create: `car-manual-rag/backend/data/manuals/.gitkeep`
- Create: `car-manual-rag/backend/tests/test_chunking.py`
- Modify: `car-manual-rag/docs/data-sources.md`

**Interfaces:**
- Consumes `Vehicle` from `app.catalog`.
- Produces `ManualChunk(id: str, text: str, metadata: dict[str, str | int])`.
- Produces `extract_chunks(pdf_path: Path, vehicle: Vehicle, manual_title: str, chunk_size: int = 900, overlap: int = 120) -> list[ManualChunk]`.

- [ ] **Step 1: Write failing extraction and metadata tests**

```python
from app.catalog import get_vehicle
from app.rag.chunking import chunk_page_text

def test_chunk_page_text_preserves_vehicle_and_page_metadata():
    chunks = chunk_page_text(
        page_text="轮胎压力不足时，请立即检查轮胎。" * 80,
        page_number=42,
        vehicle=get_vehicle("toyota-corolla"),
        manual_title="卡罗拉用户手册",
        chapter_title="轮胎",
    )
    assert chunks[0].metadata["vehicle_id"] == "toyota-corolla"
    assert chunks[0].metadata["page_number"] == 42
    assert chunks[0].metadata["chapter_title"] == "轮胎"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd car-manual-rag/backend && uv run pytest tests/test_chunking.py -v`

Expected: FAIL because `chunk_page_text` is undefined.

- [ ] **Step 3: Implement deterministic chunking**

Implement `chunk_page_text` to normalize whitespace, split text into windows of at most `chunk_size` characters with `overlap` characters, skip blank pages, and generate IDs with `{vehicle.id}-p{page_number}-c{index}`. Populate all mandatory metadata fields, including `vehicle_id`, `brand`, `model`, `year`, `manual_title`, `chapter_title`, and `page_number`. Implement `extract_chunks` with `pypdf.PdfReader`, using `第 {page_number} 页` as the chapter title until a real table-of-contents parser is added.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd car-manual-rag/backend && uv run pytest tests/test_chunking.py -v`

Expected: PASS.

- [ ] **Step 5: Add the import provenance check**

Make the future import command require a matching row in `docs/data-sources.md` whose status is `已确认可用`; it must abort with a clear message for the initial `待确认，不可导入` rows.

- [ ] **Step 6: Commit**

```bash
git add car-manual-rag/backend/app/rag/chunking.py car-manual-rag/backend/tests/test_chunking.py car-manual-rag/docs/data-sources.md
git commit -m "feat: add page-preserving manual chunking"
```

### Task 3: Add ChromaDB storage, retrieval, and manual import command

**Files:**
- Create: `car-manual-rag/backend/app/rag/store.py`
- Create: `car-manual-rag/backend/app/rag/retriever.py`
- Create: `car-manual-rag/backend/scripts/import_manual.py`
- Create: `car-manual-rag/backend/tests/test_retriever.py`

**Interfaces:**
- Consumes `list[ManualChunk]` from `app.rag.chunking`.
- Produces `ManualStore.upsert(chunks: list[ManualChunk]) -> None` and `ManualStore.query(vehicle_id: str, question: str, limit: int = 4) -> list[RetrievedChunk]`.
- Produces `retrieve_evidence(vehicle_id: str, question: str, minimum_distance: float = 1.1) -> list[RetrievedChunk]`.

- [ ] **Step 1: Write a failing vehicle-isolation test**

```python
def test_query_never_returns_chunks_for_another_vehicle(store):
    store.upsert([
        make_chunk("toyota-corolla", "轮胎压力警告"),
        make_chunk("honda-civic", "机油寿命提示"),
    ])
    results = store.query("toyota-corolla", "轮胎警告")
    assert [item.metadata["vehicle_id"] for item in results] == ["toyota-corolla"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd car-manual-rag/backend && uv run pytest tests/test_retriever.py::test_query_never_returns_chunks_for_another_vehicle -v`

Expected: FAIL because `ManualStore` is undefined.

- [ ] **Step 3: Implement local vector storage**

Wrap `chromadb.PersistentClient` in `ManualStore`. Use `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` for Chinese and English manual content. Pass `where={"vehicle_id": vehicle_id}` in every query; never rely on the LLM to enforce vehicle isolation. Return the chunk text, metadata, and Chroma distance in `RetrievedChunk`.

- [ ] **Step 4: Implement evidence thresholding and the importer**

Implement `retrieve_evidence` to discard results above `minimum_distance` and return an empty list when evidence is insufficient. Implement `python scripts/import_manual.py --vehicle-id toyota-corolla --pdf path/to/manual.pdf --title "..."`; validate the vehicle and provenance row, chunk the PDF, and upsert the chunks. Print imported chunk count and page count without exposing document text.

- [ ] **Step 5: Run retrieval tests to verify they pass**

Run: `cd car-manual-rag/backend && uv run pytest tests/test_retriever.py -v`

Expected: PASS, including vehicle isolation and insufficient-evidence cases.

- [ ] **Step 6: Commit**

```bash
git add car-manual-rag/backend/app/rag car-manual-rag/backend/scripts/import_manual.py car-manual-rag/backend/tests/test_retriever.py
git commit -m "feat: add vehicle-filtered RAG retrieval"
```

### Task 4: Ground DeepSeek answers in retrieved evidence

**Files:**
- Create: `car-manual-rag/backend/app/rag/answering.py`
- Create: `car-manual-rag/backend/tests/test_answering.py`
- Modify: `car-manual-rag/backend/app/schemas.py`

**Interfaces:**
- Consumes `question: str`, `vehicle: Vehicle`, and `evidence: list[RetrievedChunk]`.
- Produces `ChatResponse(answer: str, steps: list[str], warnings: list[str], citations: list[Citation], grounded: bool)`.
- Produces `answer_question(question: str, vehicle: Vehicle, evidence: list[RetrievedChunk]) -> ChatResponse`.

- [ ] **Step 1: Write failing no-evidence and citation tests**

```python
def test_no_evidence_returns_an_ungrounded_response(service, corolla):
    result = service.answer_question("怎样拆卸变速箱？", corolla, [])
    assert result.grounded is False
    assert result.steps == []
    assert result.citations == []

def test_grounded_response_contains_citations_from_evidence(service, corolla, evidence):
    result = service.answer_question("胎压警告是什么意思？", corolla, evidence)
    assert result.grounded is True
    assert result.citations[0].page_number == evidence[0].metadata["page_number"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd car-manual-rag/backend && uv run pytest tests/test_answering.py -v`

Expected: FAIL because `answer_question` and response schemas are undefined.

- [ ] **Step 3: Implement safe response schemas and prompt construction**

Define `Citation(manual_title, chapter_title, page_number, excerpt)` and `ChatResponse`. When evidence is empty, return a fixed Chinese no-evidence response with no steps. For non-empty evidence, construct a system prompt that permits only supplied excerpts, prohibits invented values or procedures, requires JSON fields matching `ChatResponse`, and tells the model to use neutral safety language. Build citations in Python directly from retrieved metadata, never trust model-generated page numbers.

- [ ] **Step 4: Add the DeepSeek client and parse model output**

Use `OpenAI(base_url=settings.deepseek_base_url, api_key=settings.deepseek_api_key)`. Reject requests with a server configuration error if no key is configured. Parse only `answer`, `steps`, and `warnings` from the model JSON; attach citations derived from evidence. If output is malformed, return a safe ungrounded response rather than retrying with a free-form prompt.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd car-manual-rag/backend && uv run pytest tests/test_answering.py -v`

Expected: PASS with mocked DeepSeek client responses.

- [ ] **Step 6: Commit**

```bash
git add car-manual-rag/backend/app/rag/answering.py car-manual-rag/backend/app/schemas.py car-manual-rag/backend/tests/test_answering.py
git commit -m "feat: add grounded DeepSeek answers"
```

### Task 5: Expose and test FastAPI endpoints

**Files:**
- Create: `car-manual-rag/backend/app/api/routes.py`
- Create: `car-manual-rag/backend/app/main.py`
- Create: `car-manual-rag/backend/tests/test_routes.py`

**Interfaces:**
- Produces `GET /vehicles -> list[Vehicle]`.
- Produces `GET /manuals/{vehicle_id}/chapters -> list[str]`.
- Produces `POST /chat` accepting `{"vehicle_id": str, "question": str}` and returning `ChatResponse`.

- [ ] **Step 1: Write failing API tests**

```python
def test_vehicles_endpoint_returns_three_models(client):
    response = client.get("/vehicles")
    assert response.status_code == 200
    assert len(response.json()) == 3

def test_chat_rejects_an_unsupported_vehicle(client):
    response = client.post("/chat", json={"vehicle_id": "unknown", "question": "保养周期"})
    assert response.status_code == 422

def test_chat_returns_ungrounded_when_retrieval_is_empty(client, mocker):
    mocker.patch("app.api.routes.retrieve_evidence", return_value=[])
    response = client.post("/chat", json={"vehicle_id": "toyota-corolla", "question": "保养周期"})
    assert response.json()["grounded"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd car-manual-rag/backend && uv run pytest tests/test_routes.py -v`

Expected: FAIL because the FastAPI application does not exist.

- [ ] **Step 3: Implement routes and dependency wiring**

Create FastAPI app with CORS restricted to `http://localhost:5173`. Validate nonblank questions and valid vehicle IDs. `GET /manuals/{vehicle_id}/chapters` returns the unique chapter titles in the filtered collection or an empty list before imports. `/chat` calls `retrieve_evidence` then `answer_question`; map missing API configuration to HTTP 503 and preserve safe ungrounded 200 responses for insufficient manual evidence.

- [ ] **Step 4: Run route tests to verify they pass**

Run: `cd car-manual-rag/backend && uv run pytest tests/test_routes.py -v`

Expected: PASS.

- [ ] **Step 5: Run all backend tests**

Run: `cd car-manual-rag/backend && uv run pytest -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add car-manual-rag/backend/app/api car-manual-rag/backend/app/main.py car-manual-rag/backend/tests/test_routes.py
git commit -m "feat: expose car manual RAG API"
```

### Task 6: Build the React workflow for selection, chat, and citations

**Files:**
- Create: `car-manual-rag/frontend/package.json`
- Create: `car-manual-rag/frontend/vite.config.ts`
- Create: `car-manual-rag/frontend/src/main.tsx`
- Create: `car-manual-rag/frontend/src/types.ts`
- Create: `car-manual-rag/frontend/src/api.ts`
- Create: `car-manual-rag/frontend/src/App.tsx`
- Create: `car-manual-rag/frontend/src/components/VehicleSelector.tsx`
- Create: `car-manual-rag/frontend/src/components/ChatPanel.tsx`
- Create: `car-manual-rag/frontend/src/components/AnswerCard.tsx`
- Create: `car-manual-rag/frontend/src/components/CitationCard.tsx`
- Create: `car-manual-rag/frontend/src/styles.css`
- Create: `car-manual-rag/frontend/src/App.test.tsx`

**Interfaces:**
- Consumes `GET /vehicles` and `POST /chat` defined in Task 5.
- Produces a browser flow: select vehicle, submit question, read structured answer and citation cards.

- [ ] **Step 1: Write a failing screen-flow test**

```tsx
it("disables asking until a vehicle is selected and renders returned citations", async () => {
  render(<App />);
  expect(screen.getByRole("button", { name: "开始查询" })).toBeDisabled();
  await userEvent.selectOptions(screen.getByLabelText("车型"), "toyota-corolla");
  await userEvent.type(screen.getByLabelText("问题"), "胎压警告是什么意思？");
  await userEvent.click(screen.getByRole("button", { name: "开始查询" }));
  expect(await screen.findByText("第 42 页")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd car-manual-rag/frontend && npm test -- --run`

Expected: FAIL because the Vite React app does not exist.

- [ ] **Step 3: Implement the API client and components**

Create TypeScript types that mirror `Vehicle`, `Citation`, and `ChatResponse`. Use `fetch` in `api.ts` with base URL `http://localhost:8000`. `VehicleSelector` loads `/vehicles` and stores `vehicle_id`. `ChatPanel` prevents empty questions and disabled submission without a selected vehicle. `AnswerCard` renders conclusion, numbered steps, warnings, and a clear no-evidence message when `grounded` is false. `CitationCard` renders manual title, chapter title, `第 {page_number} 页`, and quoted excerpt.

- [ ] **Step 4: Add responsive, accessible styling**

Use semantic labels, visible keyboard focus, sufficient contrast, and a single-column mobile layout that becomes a two-column desktop layout. Keep styling focused on the three defined flows; do not add authentication, user history, document uploads, or administrative controls.

- [ ] **Step 5: Run frontend tests to verify they pass**

Run: `cd car-manual-rag/frontend && npm test -- --run`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add car-manual-rag/frontend
git commit -m "feat: add vehicle-specific RAG web interface"
```

### Task 7: Complete documentation and execute the end-to-end demo check

**Files:**
- Modify: `car-manual-rag/README.md`
- Create: `car-manual-rag/docs/test-questions.md`
- Modify: `car-manual-rag/docs/data-sources.md`

**Interfaces:**
- Consumes the importer, three API endpoints, and frontend flow from Tasks 1–6.
- Produces reproducible setup instructions and manual validation evidence.

- [ ] **Step 1: Write the test-question matrix**

Create a table with columns `车型`, `问题`, `预期章节`, `预期页码`, `预期结果`. Include one supported question each for maintenance, an instrument warning, and tire-related information, plus unsupported-vehicle, no-evidence, blank-question, and cross-vehicle cases.

- [ ] **Step 2: Document reproducible setup**

Document how to create `backend/.env` from `.env.example`, add the DeepSeek key, confirm provenance, import each manual, start `uv run uvicorn app.main:app --reload`, run `npm install`, and start `npm run dev`. State that no manual can be imported until its source row is marked `已确认可用`.

- [ ] **Step 3: Run automated verification**

Run: `cd car-manual-rag/backend && uv run pytest -v`

Expected: PASS.

Run: `cd car-manual-rag/frontend && npm test -- --run`

Expected: PASS.

- [ ] **Step 4: Perform manual end-to-end verification after approved manuals are imported**

Start backend and frontend. For each imported vehicle, select it, ask its assigned supported question, and verify a displayed citation contains the expected model, chapter, and page. Submit an unsupported vehicle and a no-evidence request; verify no repair steps appear and the UI clearly reports insufficient evidence.

- [ ] **Step 5: Commit**

```bash
git add car-manual-rag/README.md car-manual-rag/docs
git commit -m "docs: add car manual RAG setup and validation guide"
```


