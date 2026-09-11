from __future__ import annotations

import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import app.rag.retriever as retriever
from app.rag.chunking import ManualChunk
from app.rag.retriever import RetrievalOptions, retrieve_evidence
from app.rag.store import ManualStore
from scripts.import_manual import provenance_is_confirmed


class FakeEmbeddingModel:
    def encode(self, values):
        return [[float(len(value)), 1.0] for value in values]


@dataclass
class FakeCollection:
    rows: dict[str, tuple[str, dict, list[float]]]

    def __init__(self):
        self.rows = {}
        self.last_query: dict | None = None

    def upsert(self, *, ids, documents, metadatas, embeddings):
        for item_id, document, metadata, embedding in zip(ids, documents, metadatas, embeddings):
            self.rows[item_id] = (document, metadata, embedding)

    def count(self):
        return len(self.rows)

    def query(self, *, query_embeddings, n_results, where, include):
        self.last_query = {"query_embeddings": query_embeddings, "n_results": n_results, "where": where, "include": include}
        matches = [
            (item_id, row)
            for item_id, row in self.rows.items()
            if row[1]["vehicle_id"] == where["vehicle_id"]
        ][:n_results]
        return {
            "ids": [[item_id for item_id, _ in matches]],
            "documents": [[row[0] for _, row in matches]],
            "metadatas": [[row[1] for _, row in matches]],
            "distances": [[0.2 if index == 0 else 1.3 for index, _ in enumerate(matches)]],
        }

    def get(self, *, where, include):
        matches = [
            (item_id, row)
            for item_id, row in self.rows.items()
            if row[1]["vehicle_id"] == where["vehicle_id"]
        ]
        return {
            "ids": [item_id for item_id, _ in matches],
            "documents": [row[0] for _, row in matches],
            "metadatas": [row[1] for _, row in matches],
        }


@pytest.fixture
def store():
    return ManualStore(collection=FakeCollection(), embedding_model=FakeEmbeddingModel())


def make_chunk(vehicle_id: str, text: str, *, chapter: str | None = None) -> ManualChunk:
    metadata = {"vehicle_id": vehicle_id, "source_text": text, "page_number": 1}
    if chapter is not None:
        metadata["chapter_title"] = chapter
    return ManualChunk(
        id=f"{vehicle_id}-{text}",
        text=text,
        metadata=metadata,
    )


def set_vector_results(store, chunks: list[ManualChunk], distances: list[float]) -> None:
    def query(**kwargs):
        limit = kwargs["n_results"]
        selected_chunks = chunks[:limit]
        return {
            "ids": [[chunk.id for chunk in selected_chunks]],
            "documents": [[chunk.text for chunk in selected_chunks]],
            "metadatas": [[chunk.metadata for chunk in selected_chunks]],
            "distances": [distances[:limit]],
        }

    store.collection.query = query


def seed_keyword_promotion_fixture(store) -> list[ManualChunk]:
    chunks = [
        ManualChunk(
            id="vector-first",
            text="发动机故障诊断提示",
            metadata={"vehicle_id": "toyota-corolla", "source_text": "发动机故障诊断提示", "page_number": 1},
        ),
        ManualChunk(
            id="dtc-generic",
            text="DTC generic",
            metadata={"vehicle_id": "toyota-corolla", "source_text": "DTC generic", "page_number": 2},
        ),
        ManualChunk(
            id="dtc-p0420",
            text="DTC P0420",
            metadata={"vehicle_id": "toyota-corolla", "source_text": "DTC P0420", "page_number": 2},
        ),
        ManualChunk(
            id="p0420-generic",
            text="P0420 generic",
            metadata={"vehicle_id": "toyota-corolla", "source_text": "P0420 generic", "page_number": 3},
        ),
        make_chunk("toyota-corolla", "保养周期"),
        make_chunk("toyota-corolla", "燃油液位"),
        make_chunk("toyota-corolla", "雨刷维护"),
        make_chunk("toyota-corolla", "座椅调节"),
    ]
    store.upsert(chunks)
    set_vector_results(store, chunks[:4], [0.2, 0.4, 0.6, 1.3])
    return chunks


def seed_threshold_before_limit_fixture(store) -> None:
    chunks = [
        ManualChunk(
            id="too-distant-dtc",
            text="DTC generic",
            metadata={"vehicle_id": "toyota-corolla", "source_text": "DTC generic", "page_number": 1},
        ),
        ManualChunk(
            id="p0420-decoy",
            text="P0420 generic",
            metadata={"vehicle_id": "toyota-corolla", "source_text": "P0420 generic", "page_number": 2},
        ),
        ManualChunk(
            id="dtc-p0420",
            text="DTC P0420",
            metadata={"vehicle_id": "toyota-corolla", "source_text": "DTC P0420", "page_number": 3},
        ),
        make_chunk("toyota-corolla", "保养周期"),
        make_chunk("toyota-corolla", "燃油液位"),
        make_chunk("toyota-corolla", "雨刷维护"),
        make_chunk("toyota-corolla", "座椅调节"),
        make_chunk("toyota-corolla", "警告灯说明"),
    ]
    store.upsert(chunks)
    set_vector_results(store, chunks[:3], [0.6, 0.4, 0.4])


def test_query_never_returns_chunks_for_another_vehicle(store):
    store.upsert([
        make_chunk("toyota-corolla", "轮胎压力警告"),
        make_chunk("honda-civic", "机油寿命提示"),
    ])

    results = store.query("toyota-corolla", "轮胎警告")

    assert [item.metadata["vehicle_id"] for item in results] == ["toyota-corolla"]
    assert store.collection.last_query["where"] == {"vehicle_id": "toyota-corolla"}


def test_list_chunks_filters_by_vehicle_and_selected_chapter(store):
    store.upsert(
        [
            make_chunk("toyota-corolla", "轮胎", chapter="轮胎"),
            make_chunk("toyota-corolla", "保养", chapter="保养"),
            make_chunk("honda-civic", "轮胎", chapter="轮胎"),
        ]
    )

    assert [
        chunk.text for chunk in store.list_chunks("toyota-corolla", {"轮胎"})
    ] == ["轮胎"]


def test_retrieval_options_uses_default_limit_chapter_scope_and_distance():
    options = RetrievalOptions()

    assert (options.limit, options.chapter_titles, options.minimum_distance) == (
        4,
        None,
        1.1,
    )


def test_retrieval_options_keeps_selected_chapters_immutable():
    options = RetrievalOptions(chapter_titles={"轮胎"})

    assert isinstance(options.chapter_titles, frozenset)
    assert options.chapter_titles == {"轮胎"}
    with pytest.raises(AttributeError):
        options.chapter_titles.add("保养")


@pytest.mark.parametrize("limit", [0, -1])
def test_retrieval_options_rejects_nonpositive_limits(limit):
    with pytest.raises(ValueError, match="limit must be positive"):
        RetrievalOptions(limit=limit)


@pytest.mark.parametrize("token_empty", [False, True])
def test_retrieve_evidence_caps_custom_limits_at_four(store, token_empty):
    chunks = [
        make_chunk("toyota-corolla", "-" * index if token_empty else f"tire {index}")
        for index in range(1, 9)
    ]
    store.upsert(chunks)
    set_vector_results(store, chunks, [0.2] * 8)

    results = retrieve_evidence(
        "toyota-corolla", "tire", options=RetrievalOptions(limit=8), store=store
    )

    assert len(results) == 4


def test_retrieve_evidence_discards_results_beyond_distance_threshold(store):
    store.upsert([
        make_chunk("toyota-corolla", "轮胎压力警告"),
        make_chunk("toyota-corolla", "无关内容"),
    ])

    results = retrieve_evidence("toyota-corolla", "轮胎警告", store=store)

    assert [(item.text, item.distance) for item in results] == [("轮胎压力警告", 0.2)]


def test_retrieve_evidence_returns_empty_when_selected_chapter_has_no_candidates(store):
    store.upsert([make_chunk("toyota-corolla", "保养周期", chapter="保养")])

    results = retrieve_evidence(
        "toyota-corolla",
        "保养周期",
        options=RetrievalOptions(chapter_titles={"轮胎"}),
        store=store,
    )

    assert results == []


@pytest.mark.parametrize("limit, expected", [(1, ["??"]), (4, ["??", "----"])])
def test_token_empty_chapter_uses_scoped_vector_order_and_distance_limit(
    store, limit, expected
):
    chunks = [
        make_chunk("toyota-corolla", "----", chapter="symbols"),
        make_chunk("toyota-corolla", "??", chapter="symbols"),
        make_chunk("toyota-corolla", "!!!", chapter="symbols"),
        make_chunk("toyota-corolla", "tire pressure", chapter="tires"),
        make_chunk("honda-civic", "....", chapter="symbols"),
    ]
    store.upsert(chunks)
    set_vector_results(
        store, [chunks[3], chunks[4], chunks[1], chunks[0], chunks[2]],
        [0.01, 0.02, 0.2, 0.3, 1.2],
    )

    results = retrieve_evidence(
        "toyota-corolla", "symbols",
        options=RetrievalOptions(limit=limit, chapter_titles={"symbols"}),
        store=store,
    )

    assert [chunk.text for chunk in results] == expected


def test_retrieve_evidence_applies_chapter_scope_before_top_k_limit(store):
    store.upsert(
        [
            make_chunk("toyota-corolla", "保养周期", chapter="保养"),
            make_chunk("toyota-corolla", "轮胎压力", chapter="轮胎"),
        ]
    )

    results = retrieve_evidence(
        "toyota-corolla",
        "轮胎压力",
        options=RetrievalOptions(
            limit=1, chapter_titles={"轮胎"}, minimum_distance=2.0
        ),
        store=store,
    )

    assert [chunk.text for chunk in results] == ["轮胎压力"]


def test_retrieve_evidence_returns_empty_when_all_results_are_insufficient(store):
    store.upsert([make_chunk("toyota-corolla", "轮胎压力警告")])
    store.collection.query = lambda **_: {
        "ids": [["toyota-corolla-轮胎压力警告"]],
        "documents": [["轮胎压力警告"]],
        "metadatas": [[{"vehicle_id": "toyota-corolla", "source_text": "轮胎压力警告"}]],
        "distances": [[1.2]],
    }

    assert retrieve_evidence("toyota-corolla", "轮胎警告", store=store) == []


def test_hybrid_retrieval_promotes_exact_keyword_match_over_vector_order(store):
    seed_keyword_promotion_fixture(store)

    assert retrieve_evidence("toyota-corolla", "DTC P0420", store=store)[0].id == "dtc-p0420"


def test_hybrid_retrieval_keeps_vector_order_when_no_lexical_term_matches(store):
    chunks = [
        make_chunk("toyota-corolla", "engine oil maintenance"),
        make_chunk("toyota-corolla", "trailer towing"),
    ]
    store.upsert(chunks)
    set_vector_results(store, chunks, [0.2, 0.3])

    results = retrieve_evidence("toyota-corolla", "中文问题", store=store)

    assert [chunk.id for chunk in results] == [chunk.id for chunk in chunks]


def test_hybrid_retrieval_expands_common_chinese_vehicle_terms_for_english_manuals(store):
    chunks = [
        make_chunk("toyota-corolla", "unrelated maintenance"),
        make_chunk("toyota-corolla", "engine oil level check"),
        make_chunk("toyota-corolla", "unrelated tire pressure"),
    ]
    store.upsert(chunks)
    set_vector_results(store, chunks, [0.2, 0.3, 0.4])

    question = "\u5982\u4f55\u68c0\u67e5\u53d1\u52a8\u673a\u673a\u6cb9\u6db2\u4f4d\uff1f"
    results = retrieve_evidence("toyota-corolla", question, store=store)

    assert results[0].id == chunks[1].id


def test_hybrid_retrieval_filters_fused_candidates_before_applying_limit(store):
    seed_threshold_before_limit_fixture(store)

    results = retrieve_evidence(
        "toyota-corolla",
        "DTC P0420",
        options=RetrievalOptions(limit=1, minimum_distance=0.5),
        store=store,
    )

    assert [chunk.id for chunk in results] == ["dtc-p0420"]


def test_baseline_retrieval_preserves_vector_order_and_distance_filter(store):
    seed_keyword_promotion_fixture(store)

    results = retriever.retrieve_baseline_evidence(
        "toyota-corolla", "DTC P0420", store=store
    )

    assert [chunk.id for chunk in results] == [
        "vector-first",
        "dtc-generic",
        "dtc-p0420",
    ]


def test_query_returns_empty_without_loading_an_embedding_model_for_an_empty_collection(
    monkeypatch,
):
    store = ManualStore(collection=FakeCollection())

    def embedding_model_must_not_be_loaded():
        raise AssertionError("an empty collection must not load an embedding model")

    monkeypatch.setattr(
        ManualStore,
        "_create_embedding_model",
        staticmethod(embedding_model_must_not_be_loaded),
    )

    assert store.query("toyota-corolla", "轮胎警告") == []


def test_embedding_model_uses_the_project_local_cache_folder(monkeypatch, tmp_path):
    cache_folder = tmp_path / "data" / "models"
    captured: dict[str, object] = {}

    class FakeSentenceTransformer:
        def __init__(self, model_name, *, cache_folder, device, local_files_only):
            captured["model_name"] = model_name
            captured["cache_folder"] = cache_folder

    monkeypatch.setattr(ManualStore, "MODEL_CACHE_DIR", cache_folder, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    ManualStore._create_embedding_model()

    assert cache_folder.is_dir()
    assert captured == {
        "model_name": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "cache_folder": str(cache_folder),
    }


def test_provenance_requires_a_confirmed_matching_vehicle_row(tmp_path):
    sources = tmp_path / "data-sources.md"
    sources.write_text(
        "| 车型 | 覆盖车型/年份 | 发布者 URL | 获取日期 | 许可或访问状态 | 本地文件名 | 状态 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| 丰田卡罗拉 | 丰田卡罗拉 2024 | url | 2026-09-09 | permitted | `manual.pdf` | 待确认，不可导入 |\n",
        encoding="utf-8",
    )

    assert not provenance_is_confirmed("toyota-corolla", sources)
    sources.write_text(
        sources.read_text(encoding="utf-8").replace("待确认，不可导入", "已确认可用"),
        encoding="utf-8",
    )
    assert provenance_is_confirmed("toyota-corolla", sources)
