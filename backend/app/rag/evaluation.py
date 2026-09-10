"""Offline retrieval evaluation; no answer generation or hosted model calls."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import yaml

from app.catalog import get_vehicle
from app.rag.retriever import retrieve_baseline_evidence, retrieve_evidence
from app.rag.store import ManualStore

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = BACKEND_ROOT / "data" / "eval-reports"


class EvaluationCase(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: str = Field(min_length=1)
    vehicle_id: str
    question: str = Field(min_length=1)
    expected_pages: list[int]
    expected_chapters: list[str]
    should_grounded: bool

    @field_validator("id", "vehicle_id", "question")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def validate_expectations(self):
        if get_vehicle(self.vehicle_id) is None:
            raise ValueError(f"{self.id}: unknown vehicle {self.vehicle_id}")
        if any(page <= 0 for page in self.expected_pages):
            raise ValueError(f"{self.id}: expected pages must be positive PDF page numbers")
        if any(not chapter.strip() for chapter in self.expected_chapters):
            raise ValueError(f"{self.id}: expected chapters must not be blank")
        if self.should_grounded and not self.expected_pages:
            raise ValueError(f"{self.id}: grounded case requires expected pages")
        if not self.should_grounded and (self.expected_pages or self.expected_chapters):
            raise ValueError(f"{self.id}: refusal case must have empty expectations")
        return self


def load_cases(path: Path) -> list[EvaluationCase]:
    try:
        rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(rows, list) or not rows:
        raise ValueError("Evaluation cases must be a nonempty YAML list")
    cases = []
    seen = set()
    for index, row in enumerate(rows, start=1):
        label = row.get("id", f"row {index}") if isinstance(row, dict) else f"row {index}"
        try:
            case = EvaluationCase.model_validate(row)
        except ValueError as exc:
            raise ValueError(f"Case {label}: {exc}") from exc
        if case.id in seen:
            raise ValueError(f"Case {case.id}: duplicate id")
        seen.add(case.id)
        cases.append(case)
    return cases


@dataclass(frozen=True)
class Metrics:
    case_count: int
    grounded_case_count: int
    recall_at_1: float | None
    recall_at_3: float | None
    mrr: float | None
    citation_candidate_precision: float | None
    refusal_accuracy: float


@dataclass(frozen=True)
class CaseResult:
    id: str
    vehicle_id: str
    question: str
    expected_pages: list[int]
    expected_chapters: list[str]
    should_grounded: bool
    returned_pages: list[int]
    hit_pages: list[int]
    first_relevant_rank: int | None
    candidates: list[dict]
    recall_at_1: float | None
    recall_at_3: float | None
    mrr: float | None
    citation_candidate_precision: float | None
    refusal_correct: bool
    failure_reasons: list[str]


@dataclass(frozen=True)
class EvaluationReport:
    mode: str
    created_at: str
    overall: Metrics
    by_vehicle: dict[str, Metrics]
    cases: list[CaseResult]


def _aggregate(results: list[CaseResult]) -> Metrics:
    grounded = [result for result in results if result.should_grounded]
    def average(name):
        return mean(getattr(result, name) for result in grounded) if grounded else None
    return Metrics(len(results), len(grounded), average("recall_at_1"),
                   average("recall_at_3"), average("mrr"),
                   average("citation_candidate_precision"),
                   mean(float(result.refusal_correct) for result in results))


def evaluate_cases(cases: list[EvaluationCase], mode: str, store: ManualStore) -> EvaluationReport:
    if mode not in {"baseline", "hybrid"}:
        raise ValueError(f"Unknown retrieval mode: {mode}")
    if not cases:
        raise ValueError("Evaluation cases must not be empty")
    seen = set()
    imported = {}
    # Validate the whole suite before retrieval so a bad case cannot yield a partial report.
    for case in cases:
        if case.id in seen:
            raise ValueError(f"Case {case.id}: duplicate id")
        seen.add(case.id)
        if case.should_grounded:
            if case.vehicle_id not in imported:
                imported[case.vehicle_id] = bool(store.list_chunks(case.vehicle_id))
            if not imported[case.vehicle_id]:
                raise ValueError(f"Case {case.id}: vehicle {case.vehicle_id} has no imported chunks")
    retrieve = retrieve_baseline_evidence if mode == "baseline" else retrieve_evidence
    results = []
    for case in cases:
        # Expected chapters are ground truth, never passed as a retrieval filter.
        evidence = retrieve(case.vehicle_id, case.question, store=store)[:4]
        pages = [int(chunk.metadata["page_number"]) for chunk in evidence]
        expected = set(case.expected_pages)
        rank = next((i for i, page in enumerate(pages, 1) if page in expected), None)
        unique = {}
        candidates = []
        for i, chunk in enumerate(evidence, 1):
            candidate = dict(rank=i, chunk_id=chunk.id,
                             manual_title=chunk.metadata.get("manual_title", ""),
                             page_number=int(chunk.metadata["page_number"]),
                             chapter_title=chunk.metadata.get("chapter_title", ""),
                             distance=chunk.distance)
            candidates.append(candidate)
            unique.setdefault((candidate["manual_title"], candidate["page_number"]), candidate)
        correct = bool(evidence) == case.should_grounded
        failures = []
        if case.should_grounded:
            if not evidence:
                failures.append("no_evidence")
            if rank is None:
                failures.append("expected_page_not_retrieved")
        elif evidence:
            failures.append("unexpected_evidence")
        precision = (sum(c["page_number"] in expected for c in unique.values()) / len(unique)
                     if unique else 0.0)
        results.append(CaseResult(
            **case.model_dump(), returned_pages=pages,
            hit_pages=list(dict.fromkeys(p for p in pages if p in expected)),
            first_relevant_rank=rank, candidates=candidates,
            recall_at_1=float(rank == 1) if case.should_grounded else None,
            recall_at_3=float(rank is not None and rank <= 3) if case.should_grounded else None,
            mrr=(1 / rank if rank else 0.0) if case.should_grounded else None,
            citation_candidate_precision=precision if case.should_grounded else None,
            refusal_correct=correct, failure_reasons=failures,
        ))
    return EvaluationReport(mode, datetime.now(timezone.utc).isoformat(), _aggregate(results),
                            {v: _aggregate([r for r in results if r.vehicle_id == v])
                             for v in sorted({r.vehicle_id for r in results})}, results)


def write_reports(report: EvaluationReport) -> tuple[Path, Path]:
    """Write only to the ignored local report folder, regardless of the caller's cwd."""
    if report.mode not in {"baseline", "hybrid"}:
        raise ValueError("Invalid report mode")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{report.mode}"
    json_path, markdown_path = REPORT_DIR / f"{stem}.json", REPORT_DIR / f"{stem}.md"
    json_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    def display(value):
        return "N/A" if value is None else f"{value:.4f}"
    def escape(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = [f"# Retrieval evaluation: {report.mode}", "", f"UTC: {report.created_at}", "",
             "Relevance metrics average grounded cases only; N/A means no grounded cases. "
             "Refusal accuracy covers all cases. Ranks use raw top-four chunks; candidate precision "
             "deduplicates by manual title and PDF page. Expected chapters are labels, not filters.", "",
             "| Scope | Cases | Grounded | Recall@1 | Recall@3 | MRR | citation_candidate_precision | refusal_accuracy |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for scope, metrics in [("overall", report.overall), *report.by_vehicle.items()]:
        lines.append(f"| {scope} | {metrics.case_count} | {metrics.grounded_case_count} | " +
                     " | ".join(display(getattr(metrics, name)) for name in
                                ["recall_at_1", "recall_at_3", "mrr", "citation_candidate_precision", "refusal_accuracy"]) + " |")
    lines += ["", "| Case | Vehicle | Question | Expected pages / chapters | Returned pages (rank order) | Hit pages | First rank | Failures |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for result in report.cases:
        lines.append("| " + " | ".join(escape(v) for v in [
            result.id, result.vehicle_id, result.question,
            f"{result.expected_pages} / {result.expected_chapters}", result.returned_pages,
            result.hit_pages, result.first_relevant_rank or "N/A", ", ".join(result.failure_reasons) or "none"]) + " |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path
