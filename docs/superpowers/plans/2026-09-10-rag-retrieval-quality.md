# RAG 检索质量控制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为汽车手册 RAG 建立人工评测、混合检索、章节过滤、阈值拒答与可靠引用。

**Architecture:** `ManualStore` 读取受车型和章节限制的候选块；`retriever.py` 执行向量与 BM25 排序并用 RRF 融合。回答层只返回模型明确选择、且按手册页去重的引用。离线脚本读取人工 YAML 题集，比较基线与混合检索指标。

**Tech Stack:** Python 3.11、FastAPI、Chroma、sentence-transformers、rank-bm25、PyYAML、pytest、React/Vite。

**Spec:** `docs/superpowers/specs/2026-09-10-rag-retrieval-quality-design.md`

## Global Constraints

- 所有结果必须按 `vehicle_id` 隔离；章节过滤为空时返回无证据，绝不跨车型补充。
- 默认距离阈值为 `1.1`，RRF 常量为 `60`，回答层最多接收 4 个证据。
- 评测仅读取本地库，不调用 DeepSeek；报告只写入忽略的 `backend/data/eval-reports/`。
- 每个生产行为先写失败测试、观察失败，再写最小实现。

---

### Task 1: 候选块读取、章节过滤和检索选项

**Files:**
- Modify: `backend/app/rag/store.py`
- Modify: `backend/app/rag/retriever.py`
- Test: `backend/tests/test_retriever.py`

**Interfaces:**
- `ManualStore.list_chunks(vehicle_id: str, chapter_titles: set[str] | None = None) -> list[RetrievedChunk]`
- `RetrievalOptions(limit: int = 4, chapter_titles: set[str] | None = None, minimum_distance: float = 1.1)`

- [ ] **Step 1: Write the failing test**

```python
def test_list_chunks_filters_by_vehicle_and_selected_chapter(store):
    store.upsert([
        make_chunk("toyota-corolla", "轮胎", chapter="轮胎"),
        make_chunk("toyota-corolla", "保养", chapter="保养"),
        make_chunk("honda-civic", "轮胎", chapter="轮胎"),
    ])
    assert [chunk.text for chunk in store.list_chunks("toyota-corolla", {"轮胎"})] == ["轮胎"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_retriever.py -v`
Expected: FAIL because `list_chunks` and `RetrievalOptions` do not exist.

- [ ] **Step 3: Write minimal implementation**

Extend `VectorCollection` with Chroma `get(where, include)`. Convert IDs, documents and metadatas to `RetrievedChunk` objects, use mandatory vehicle filtering, then filter `chapter_title` in Python. Add immutable `RetrievalOptions` and preserve callers that omit it.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_retriever.py -v`

- [ ] **Step 5: Commit**

```powershell
git add backend/app/rag/store.py backend/app/rag/retriever.py backend/tests/test_retriever.py
git commit -m "feat: add chapter-scoped retrieval candidates"
```

### Task 2: BM25、RRF 融合与距离阈值

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/rag/retriever.py`
- Test: `backend/tests/test_retriever.py`

**Interfaces:**
- `retrieve_baseline_evidence(vehicle_id, question, minimum_distance=1.1, *, store=None) -> list[RetrievedChunk]`
- `retrieve_evidence(vehicle_id, question, options=None, *, store=None) -> list[RetrievedChunk]`

- [ ] **Step 1: Write the failing tests**

```python
def test_hybrid_retrieval_promotes_exact_keyword_match_over_vector_order(store):
    assert retrieve_evidence("toyota-corolla", "DTC P0420", store=store)[0].id == "dtc-p0420"

def test_hybrid_retrieval_discards_fused_chunks_beyond_distance_threshold(store):
    results = retrieve_evidence("toyota-corolla", "轮胎", options=RetrievalOptions(minimum_distance=0.5), store=store)
    assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_retriever.py -v`

- [ ] **Step 3: Write minimal implementation**

Add `rank-bm25` dependency. Rank same-scope text using `BM25Okapi` and vector results. Fuse ranks by `1 / (60 + rank)`, sort by fused score descending then vector distance ascending, filter `distance > minimum_distance`, cap at `options.limit`. Retain pure-vector behavior in `retrieve_baseline_evidence`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest -v`

- [ ] **Step 5: Commit**

```powershell
git add backend/pyproject.toml backend/app/rag/retriever.py backend/tests/test_retriever.py
git commit -m "feat: add hybrid RAG retrieval and reranking"
```

### Task 3: 模型选择引用和手册页去重

**Files:**
- Modify: `backend/app/rag/answering.py`
- Test: `backend/tests/test_answering.py`

**Interfaces:**
- Model JSON accepts `citation_ids: list[str]`.
- `_citations_from_evidence(evidence, citation_ids) -> list[Citation]`

- [ ] **Step 1: Write the failing tests**

```python
def test_answer_uses_only_model_selected_citation_ids(service, corolla, evidence):
    service.client.chat.completions.create = fake_completion_with('{"answer":"检查轮胎","steps":[],"warnings":[],"citation_ids":["chunk-2"]}')
    assert [citation.page_number for citation in result.citations] == [2]

def test_answer_deduplicates_selected_chunks_from_same_manual_page(service, corolla, same_page_evidence):
    assert len(result.citations) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_answering.py -v`

- [ ] **Step 3: Write minimal implementation**

Include `chunk_id` in the prompt and require `citation_ids`. Retain only IDs present in evidence; deduplicate by `(manual_title, page_number)`. Invalid, missing or empty IDs fall back to unique pages in evidence rank order. Keep `ChatResponse` unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_answering.py -v`

- [ ] **Step 5: Commit**

```powershell
git add backend/app/rag/answering.py backend/tests/test_answering.py
git commit -m "feat: return deduplicated evidence citations"
```

### Task 4: API 章节过滤

**Files:**
- Modify: `backend/app/api/routes.py`
- Modify: `backend/app/schemas.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- `ChatRequest.chapter_titles: list[str] | None`
- `askQuestion(vehicleId, question, chapterTitles?)`

- [ ] **Step 1: Write the failing test**

```python
def test_chat_passes_selected_chapters_to_retrieval(monkeypatch):
    captured = {}
    monkeypatch.setattr("app.api.routes.retrieve_evidence", lambda *args, **kwargs: captured.update(kwargs) or [])
    response = TestClient(app).post("/chat", json={"vehicle_id":"toyota-corolla", "question":"轮胎", "chapter_titles":["轮胎"]})
    assert response.status_code == 200
    assert captured["options"].chapter_titles == {"轮胎"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_routes.py -v`

- [ ] **Step 3: Write minimal implementation**

Validate nonblank chapter titles; create `RetrievalOptions` in the route. Update frontend request types and API serialization. Do not add UI controls in this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest -v`
Run: `npm test -- --run`
Run: `npm run build`

- [ ] **Step 5: Commit**

```powershell
git add backend/app/api/routes.py backend/app/schemas.py backend/tests/test_routes.py frontend/src/types.ts frontend/src/api.ts
git commit -m "feat: support chapter-filtered RAG queries"
```

### Task 5: 人工题集、指标与离线报告

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/evals/cases.yaml`
- Create: `backend/app/rag/evaluation.py`
- Create: `backend/scripts/evaluate_retrieval.py`
- Create: `backend/tests/test_evaluation.py`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- `EvaluationCase`, `evaluate_cases(cases, mode, store) -> EvaluationReport`
- CLI: `./.venv/Scripts/python.exe scripts/evaluate_retrieval.py --mode baseline|hybrid`

- [ ] **Step 1: Write the failing tests**

```python
def test_evaluation_calculates_recall_mrr_and_candidate_precision():
    report = evaluate_cases([matching_case], mode="hybrid", store=fake_store)
    assert report.overall.recall_at_1 == 1.0
    assert report.overall.mrr == 1.0

def test_empty_evidence_is_correct_for_refusal_case():
    assert evaluate_cases([refusal_case], mode="hybrid", store=fake_store).overall.refusal_accuracy == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_evaluation.py -v`

- [ ] **Step 3: Write minimal implementation**

Add `pyyaml`. Validate YAML case IDs, vehicles, questions and expected pages. Calculate Recall@1, Recall@3, MRR, candidate precision and refusal accuracy per vehicle and overall. Write timestamped JSON and Markdown reports locally. Create 6 manually verified Civic cases, 4 Toyota cases from pages 16–18, and 4 BYD refusal cases. Ignore the report directory in Git.

- [ ] **Step 4: Run tests and both real modes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_evaluation.py -v`
Run: `./.venv/Scripts/python.exe scripts/evaluate_retrieval.py --mode baseline`
Run: `./.venv/Scripts/python.exe scripts/evaluate_retrieval.py --mode hybrid`

- [ ] **Step 5: Document and commit**

Document commands and metric definitions in `README.md`.

```powershell
git add backend/pyproject.toml backend/evals backend/app/rag/evaluation.py backend/scripts/evaluate_retrieval.py backend/tests/test_evaluation.py .gitignore README.md
git commit -m "feat: add offline RAG retrieval evaluation"
```

### Task 6: 完整回归与结果记录

**Files:**
- Modify: `docs/test-questions.md`

- [ ] **Step 1: Run all checks**

Run: `./.venv/Scripts/python.exe -m pytest -v`
Run: `npm test -- --run`
Run: `npm run build`

- [ ] **Step 2: Record measured outcome**

Write the generated baseline and hybrid aggregate metrics, number of cases, and known failures into `docs/test-questions.md`. Only claim an improvement if both saved reports prove it.

- [ ] **Step 3: Commit**

```powershell
git add docs/test-questions.md
git commit -m "docs: record RAG evaluation results"
```
