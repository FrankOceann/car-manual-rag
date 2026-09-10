from dataclasses import asdict
import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def evaluation():
    # Import at test time so the missing feature produces an explicit red assertion.
    assert importlib.util.find_spec("app.rag.evaluation"), "offline evaluation module is missing"
    return importlib.import_module("app.rag.evaluation")


def case_data(**overrides):
    return dict(id="matching", vehicle_id="honda-civic", question="engine oil",
                expected_pages=[2], expected_chapters=["第 2 页"],
                should_grounded=True) | overrides


class RankedStore:
    """Only storage/embedding I/O is replaced; both real retrieval modes run."""

    def __init__(self, pages=(1, 1, 2, 3), vehicle="honda-civic"):
        from app.rag.store import RetrievedChunk
        self.rows = [RetrievedChunk(str(i), "engine oil", {
            "vehicle_id": vehicle, "manual_title": "manual", "page_number": p,
            "chapter_title": f"第 {p} 页"}, 0.2) for i, p in enumerate(pages)]
        self.collection = SimpleNamespace(count=lambda: len(self.rows))

    def list_chunks(self, vehicle_id, chapter_titles=None):
        return [r for r in self.rows if r.metadata["vehicle_id"] == vehicle_id and
                (chapter_titles is None or r.metadata["chapter_title"] in chapter_titles)]

    def query(self, vehicle_id, question, limit=4):
        return self.list_chunks(vehicle_id)[:limit]


@pytest.mark.parametrize("mode", ["baseline", "hybrid"])
def test_metrics_use_raw_ranks_but_deduplicate_candidate_precision(evaluation, mode):
    report = evaluation.evaluate_cases([evaluation.EvaluationCase(**case_data())], mode, RankedStore())
    assert report.overall.recall_at_1 == 0
    assert report.overall.recall_at_3 == 1
    assert report.overall.mrr == pytest.approx(1 / 3)
    assert report.overall.citation_candidate_precision == pytest.approx(1 / 3)
    assert report.overall.refusal_accuracy == 1
    assert report.cases[0].returned_pages == [1, 1, 2, 3]
    assert report.cases[0].hit_pages == [2]
    assert report.cases[0].first_relevant_rank == 3
    assert asdict(report.by_vehicle["honda-civic"]) == asdict(report.overall)


def test_empty_evidence_is_correct_for_refusal_and_excluded_from_relevance_averages(evaluation):
    cases = [evaluation.EvaluationCase(**case_data()), evaluation.EvaluationCase(**case_data(
        id="refusal", vehicle_id="byd-seal", expected_pages=[], expected_chapters=[], should_grounded=False))]
    report = evaluation.evaluate_cases(cases, "hybrid", RankedStore(pages=(2,)))
    assert report.overall.recall_at_1 == 1
    assert report.overall.mrr == 1
    assert report.overall.refusal_accuracy == 1
    assert report.overall.case_count == 2
    assert report.overall.grounded_case_count == 1
    assert report.by_vehicle["byd-seal"].mrr is None
    assert report.cases[1].returned_pages == []
    assert report.cases[1].failure_reasons == []


def test_false_refusal_and_unexpected_evidence_have_diagnostic_failures(evaluation):
    store = RankedStore(pages=(1,))
    store.query = lambda *args, **kwargs: []
    report = evaluation.evaluate_cases([evaluation.EvaluationCase(**case_data())], "baseline", store)
    assert report.overall.refusal_accuracy == 0
    assert report.overall.citation_candidate_precision == 0
    assert "no_evidence" in report.cases[0].failure_reasons
    refusal = evaluation.EvaluationCase(**case_data(id="refusal", expected_pages=[],
        expected_chapters=[], should_grounded=False))
    report = evaluation.evaluate_cases([refusal], "hybrid", RankedStore())
    assert report.overall.refusal_accuracy == 0
    assert "unexpected_evidence" in report.cases[0].failure_reasons


@pytest.mark.parametrize("overrides", [
    {"id": " "}, {"vehicle_id": "unknown"}, {"question": " "},
    {"expected_pages": []}, {"expected_pages": [0]}, {"expected_pages": [True]},
    {"expected_pages": ["2"]}, {"expected_chapters": [""]},
    {"should_grounded": False}, {"should_grounded": "false"},
])
def test_case_validation_names_invalid_case(evaluation, overrides):
    with pytest.raises(ValueError):
        evaluation.EvaluationCase(**case_data(**overrides))


def test_yaml_errors_include_case_id_and_reject_duplicates(evaluation, tmp_path):
    source = tmp_path / "cases.yaml"
    for rows, label in [([case_data(question="")], "matching"),
                        ([case_data(), case_data()], "matching")]:
        source.write_text(yaml.safe_dump(rows), encoding="utf-8")
        with pytest.raises(ValueError, match=label):
            evaluation.load_cases(source)


def test_grounded_case_requires_imported_vehicle(evaluation):
    with pytest.raises(ValueError, match="matching"):
        evaluation.evaluate_cases([evaluation.EvaluationCase(**case_data())], "baseline", RankedStore(pages=()))


def test_expected_chapters_are_labels_not_retrieval_filters(evaluation):
    result = evaluation.evaluate_cases([evaluation.EvaluationCase(**case_data())], "hybrid", RankedStore(pages=(1,)))
    assert result.cases[0].returned_pages == [1]
    assert "expected_page_not_retrieved" in result.cases[0].failure_reasons


def test_empty_suite_and_invalid_mode_rejected(evaluation):
    with pytest.raises(ValueError):
        evaluation.evaluate_cases([], "hybrid", RankedStore())
    with pytest.raises(ValueError):
        evaluation.evaluate_cases([evaluation.EvaluationCase(**case_data())], "other", RankedStore())


def test_reports_are_unique_json_and_markdown_only_in_local_report_directory(evaluation):
    report = evaluation.evaluate_cases([evaluation.EvaluationCase(**case_data())], "baseline", RankedStore())
    paths = [*evaluation.write_reports(report), *evaluation.write_reports(report)]
    try:
        assert len(set(paths)) == 4
        assert all(p.parent == ROOT / "data" / "eval-reports" for p in paths)
        payload = json.loads(paths[0].read_text(encoding="utf-8"))
        assert payload["cases"][0]["first_relevant_rank"] == 3
        assert payload["mode"] == "baseline"
        markdown = paths[1].read_text(encoding="utf-8")
        assert "matching" in markdown and "Recall@1" in markdown and "honda-civic" in markdown
    finally:
        for p in paths:
            p.unlink(missing_ok=True)


def test_cli_invalid_case_exits_nonzero_without_creating_reports(tmp_path):
    source = tmp_path / "invalid.yaml"
    source.write_text(yaml.safe_dump([case_data(id="bad-id", vehicle_id="unknown")]), encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/evaluate_retrieval.py"),
                             "--cases", str(source), "--mode", "baseline"],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    assert "bad-id" in result.stderr


def test_shipped_case_counts_and_toyota_page_scope(evaluation):
    cases = evaluation.load_cases(ROOT / "evals/cases.yaml")
    assert len(cases) == 14
    assert sum(c.vehicle_id == "honda-civic" and c.should_grounded for c in cases) == 6
    assert sum(c.vehicle_id == "toyota-corolla" and c.should_grounded for c in cases) == 4
    assert sum(c.vehicle_id == "byd-seal" and not c.should_grounded for c in cases) == 4
    assert all(set(c.expected_pages) <= {16, 17, 18} for c in cases if c.vehicle_id == "toyota-corolla")


def test_cli_runs_from_another_cwd_with_relative_chroma_path(monkeypatch, tmp_path, capsys):
    from scripts import evaluate_retrieval as cli
    source = tmp_path / "cases.yaml"
    source.write_text(yaml.safe_dump([case_data()]), encoding="utf-8")
    # HNSW on this Windows host rejects the Unicode absolute path; relative works.
    def open_store(*, chroma_path):
        if Path(chroma_path).is_absolute() or Path.cwd() != ROOT:
            raise OSError("HNSW cannot load index using absolute Unicode workspace path")
        return RankedStore()
    monkeypatch.setattr(cli, "ManualStore", open_store)
    monkeypatch.setattr(cli, "Settings", lambda **kwargs: SimpleNamespace(chroma_path="data/chroma"))
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.chdir(tmp_path)
    paths = []
    try:
        assert cli.main(["--mode", "baseline", "--cases", str(source)]) == 0
        paths = [Path(line) for line in capsys.readouterr().out.splitlines()]
        assert len(paths) == 2 and all(p.parent == ROOT / "data/eval-reports" for p in paths)
        assert json.loads(paths[0].read_text(encoding="utf-8"))["overall"]["recall_at_3"] == 1
        assert Path.cwd() == tmp_path
    finally:
        for p in paths:
            p.unlink(missing_ok=True)
