from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.rag.chunking import ManualChunk
from app.rag.retriever import retrieve_evidence
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


@pytest.fixture
def store():
    return ManualStore(collection=FakeCollection(), embedding_model=FakeEmbeddingModel())


def make_chunk(vehicle_id: str, text: str) -> ManualChunk:
    return ManualChunk(
        id=f"{vehicle_id}-{text}",
        text=text,
        metadata={"vehicle_id": vehicle_id, "source_text": text, "page_number": 1},
    )


def test_query_never_returns_chunks_for_another_vehicle(store):
    store.upsert([
        make_chunk("toyota-corolla", "轮胎压力警告"),
        make_chunk("honda-civic", "机油寿命提示"),
    ])

    results = store.query("toyota-corolla", "轮胎警告")

    assert [item.metadata["vehicle_id"] for item in results] == ["toyota-corolla"]
    assert store.collection.last_query["where"] == {"vehicle_id": "toyota-corolla"}


def test_retrieve_evidence_discards_results_beyond_distance_threshold(store):
    store.upsert([
        make_chunk("toyota-corolla", "轮胎压力警告"),
        make_chunk("toyota-corolla", "无关内容"),
    ])

    results = retrieve_evidence("toyota-corolla", "轮胎警告", store=store)

    assert [(item.text, item.distance) for item in results] == [("轮胎压力警告", 0.2)]


def test_retrieve_evidence_returns_empty_when_all_results_are_insufficient(store):
    store.upsert([make_chunk("toyota-corolla", "轮胎压力警告")])
    store.collection.query = lambda **_: {
        "ids": [["toyota-corolla-轮胎压力警告"]],
        "documents": [["轮胎压力警告"]],
        "metadatas": [[{"vehicle_id": "toyota-corolla", "source_text": "轮胎压力警告"}]],
        "distances": [[1.2]],
    }

    assert retrieve_evidence("toyota-corolla", "轮胎警告", store=store) == []


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
