"""What a run writes: evals/results/<run>/{run.json, cases.jsonl}.

Each case record carries its own expectation and oracle (copied from the
dataset), so a result directory can be scored on its own long after the
dataset has changed.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from core.enums import FindingStatus, Severity
from core.review.schemas import Category
from evals.cases import ExpectedFinding, Kind, OracleNode, Split

RUN_FILE = "run.json"
CASES_FILE = "cases.jsonl"


class Prediction(BaseModel):
    """One finding the model produced, posted or dropped by validation."""

    file: str
    line_start: int
    line_end: int
    category: Category
    severity: Severity
    confidence: float
    title: str
    status: FindingStatus
    drop_reason: str | None = None
    anchored_from: tuple[int, int] | None = None  # the model's lines, if validation moved it


class Retrieved(BaseModel):
    """One retrieved candidate, in rank order (best first)."""

    qualified_name: str
    ordinal: int
    similarity: float
    included: bool  # made it into the prompt


class CaseRecord(BaseModel):
    case_id: str
    kind: Kind
    split: Split
    operator: str
    source: str
    expected: list[ExpectedFinding]
    oracle: list[OracleNode]
    # "error": the pipeline raised before producing a review (bad revision...)
    status: Literal["completed", "failed", "error"]
    error: str | None = None
    review_id: int | None = None
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    chunks: int = 0
    failed_chunks: int = 0
    fallback_chunks: int = 0  # thinking ran out; the chunk was reviewed again without it
    findings: list[Prediction] = []
    retrieved: list[Retrieved] = []

    @property
    def posted(self) -> list[Prediction]:
        return [p for p in self.findings if p.status is FindingStatus.POSTED]


class RunInfo(BaseModel):
    """Everything needed to say what produced the numbers."""

    name: str
    config: dict[str, object]  # the full PipelineConfig
    llm_model: str
    embed_model: str
    model_digests: dict[str, str]  # model -> Ollama digest ("fake" for fakes)
    context_window: int
    temperature: float
    seed: int
    codelens_commit: str | None
    dataset_hash: str  # sha256 over the selected cases' case.yaml + patch.diff
    cases: int
    filters: dict[str, object]
    started_at: str


def write_run(directory: Path, info: RunInfo) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / RUN_FILE).write_text(info.model_dump_json(indent=2), encoding="utf-8")


def append_case(directory: Path, record: CaseRecord) -> None:
    """One line per finished case: an interrupted run keeps what it did."""
    with (directory / CASES_FILE).open("a", encoding="utf-8", newline="\n") as out:
        out.write(record.model_dump_json() + "\n")


def load_run(directory: Path) -> tuple[RunInfo, list[CaseRecord]]:
    info = RunInfo.model_validate_json((directory / RUN_FILE).read_text(encoding="utf-8"))
    path = directory / CASES_FILE
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    records = {r.case_id: r for r in (CaseRecord.model_validate_json(x) for x in lines if x)}
    return info, sorted(records.values(), key=lambda r: r.case_id)  # a re-run case: last wins
