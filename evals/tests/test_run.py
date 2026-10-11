"""The runner end to end on the toy dataset: real Postgres, real git, the real
pipeline, with fake models (CI has no GPU)."""

import asyncio
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from core.config import get_settings
from core.db import create_engine
from core.providers import Message, Providers
from core.providers.fake import FakeEmbedder, FakeLLM
from core.review.config import PipelineConfig
from evals.records import load_run
from evals.report import write_scorecard
from evals.run import case_commit, prepare_source, run_cases, select_cases
from evals.tests.toy import generate, git, make_toy_source

pytestmark = pytest.mark.integration


def flag_first_added_line(messages: Sequence[Message], schema: type[BaseModel]) -> dict[str, Any]:
    """A reviewer that always calls the first added line a critical bug."""
    prompt = messages[-1].content
    path = re.search(r"^\+\+\+ (\S+)", prompt, re.MULTILINE)
    line = re.search(r"^\s*(\d+) \+", prompt, re.MULTILINE)
    if path is None or line is None:  # a pure deletion adds no line
        return {"summary": "Nothing added.", "findings": []}
    number = int(line.group(1))
    finding = {
        "file_path": path.group(1),
        "line_start": number,
        "line_end": number,
        "severity": "critical",
        "category": "bug",
        "title": "Suspicious change",
        "explanation": "It changed.",
        "evidence": [],
        "confidence": 0.9,
    }
    return {"summary": "A change.", "findings": [finding]}


def test_run_records_scores_and_resumes(tmp_path: Path, migrated_database_url: str) -> None:
    source = make_toy_source(tmp_path / "upstream")
    generate(source, tmp_path / "checkouts", tmp_path / "dataset")
    cases = select_cases(tmp_path / "dataset")
    out = tmp_path / "results" / "run"
    out.mkdir(parents=True)
    (out / "run.json").write_text(_run_json(len(cases)), encoding="utf-8")

    async def go() -> None:
        engine = create_engine(migrated_database_url, pooled=False)
        providers = Providers(
            FakeEmbedder(get_settings().embed_dim), FakeLLM(flag_first_added_line)
        )
        try:
            prepared = {
                "toy": await prepare_source(
                    engine,
                    providers,
                    source,
                    tmp_path / "checkouts",
                    embed_max_tokens=1024,
                    say=_quiet,
                )
            }
            for _ in range(2):  # the second pass must find nothing left to do
                await run_cases(
                    engine,
                    providers,
                    cases,
                    prepared,
                    config=PipelineConfig(),
                    context_window=8192,
                    out=out,
                    say=_quiet,
                )
        finally:
            await engine.dispose()

    asyncio.run(go())

    _, records = load_run(out)
    assert sorted(r.case_id for r in records) == sorted(c.id for c, _ in cases)
    assert len((out / "cases.jsonl").read_text(encoding="utf-8").splitlines()) == len(cases)
    assert all(r.status == "completed" and r.review_id for r in records)
    assert any(r.retrieved for r in records)  # B0 retrieved context

    write_scorecard(out, resamples=50)
    card = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
    metrics = {name: m["value"] for name, m in card["metrics"].items()}
    # The fake flags the first added line: right on some seeded cases, and
    # exactly one false alarm on every clean case.
    assert 0 < metrics["recall"] < 1
    assert metrics["noise_per_clean_pr"] == 1.0
    assert card["cases"]["errors"] == 0
    assert (out / "scorecard.md").exists()


def test_case_commit_is_deterministic(tmp_path: Path) -> None:
    source = make_toy_source(tmp_path / "upstream")
    generate(source, tmp_path / "checkouts", tmp_path / "dataset")
    case, patch = select_cases(tmp_path / "dataset", operators=["drop_param"])[0]
    repo = tmp_path / "checkouts" / "toy"

    first = case_commit(repo, case, patch)

    assert case_commit(repo, case, patch) == first
    assert git(repo, "rev-parse", f"{first}^") == case.sha
    assert git(repo, "status", "--porcelain") == ""  # the working tree is untouched
    changed = git(repo, "diff", "--name-only", case.sha, first)
    assert changed == case.file


def _quiet(_: str) -> None:
    pass


def _run_json(cases: int) -> str:
    return (
        '{"name": "run", "config": {"name": "b0"}, "llm_model": "fake-llm", '
        '"embed_model": "fake", "model_digests": {}, "context_window": 8192, '
        '"temperature": 0.1, "seed": 42, "codelens_commit": null, "dataset_hash": "x", '
        f'"cases": {cases}, "filters": {{}}, "started_at": "now"}}'
    )
