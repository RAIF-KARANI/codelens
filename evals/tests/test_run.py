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
from evals.records import CaseRecord, append_case, load_run
from evals.report import write_scorecard
from evals.run import case_commits, case_fields, pending, prepare_source, run_cases, select_cases
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


def test_run_records_scores_and_resumes(
    tmp_path: Path, migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
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
                    Path("checkouts"),  # relative, as the CLI passes it
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

    monkeypatch.chdir(tmp_path)
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
    # Counterfactuals ran on their prep commits, indexed from the worktree, and
    # the fake's finding on each is a false alarm.
    assert card["cases"]["counterfactual"] > 0
    assert metrics["false_alarms_per_counterfactual"] == 1.0
    assert (tmp_path / "worktrees" / "toy" / ".git").exists()
    assert not (tmp_path / "checkouts" / "toy" / "checkouts").exists()  # not nested in the source
    assert (out / "scorecard.md").exists()


def test_case_commits_are_deterministic_and_say_nothing_about_the_bug(tmp_path: Path) -> None:
    source = make_toy_source(tmp_path / "upstream")
    generate(source, tmp_path / "checkouts", tmp_path / "dataset")
    case, patch = select_cases(tmp_path / "dataset", operators=["drop_param"])[0]
    repo = tmp_path / "checkouts" / "toy"

    base, head = case_commits(repo, case, patch)

    assert case_commits(repo, case, patch) == (base, head)
    assert base == case.sha and git(repo, "rev-parse", f"{head}^") == case.sha
    assert git(repo, "status", "--porcelain") == ""  # the working tree is untouched
    assert git(repo, "diff", "--name-only", base, head) == case.file
    # The subject reaches the prompt: it must not name the operator (D77).
    subject = git(repo, "log", "-1", "--format=%s", head)
    assert subject == f"Update {case.file}" and case.operator not in subject


def test_counterfactual_head_sits_on_its_prep_commit(tmp_path: Path) -> None:
    source = make_toy_source(tmp_path / "upstream")
    generate(source, tmp_path / "checkouts", tmp_path / "dataset")
    case, patch = select_cases(tmp_path / "dataset", operators=["drop_param_cf"])[0]
    repo = tmp_path / "checkouts" / "toy"

    base, head = case_commits(repo, case, patch)

    assert git(repo, "rev-parse", f"{base}^") == case.sha
    assert git(repo, "rev-parse", f"{head}^") == base
    # The reviewed diff touches only the function; the callers changed in the base.
    assert git(repo, "diff", "--name-only", base, head) == case.file
    assert "pkg/api.py" in git(repo, "diff", "--name-only", case.sha, base)


def _quiet(_: str) -> None:
    pass


def _run_json(cases: int) -> str:
    return (
        '{"name": "run", "config": {"name": "b0"}, "llm_model": "fake-llm", '
        '"embed_model": "fake", "model_digests": {}, "context_window": 8192, '
        '"temperature": 0.1, "seed": 42, "codelens_commit": null, "dataset_hash": "x", '
        f'"cases": {cases}, "filters": {{}}, "started_at": "now"}}'
    )


def test_pending_retries_failures_only_when_asked(tmp_path: Path) -> None:
    source = make_toy_source(tmp_path / "upstream")
    generate(source, tmp_path / "checkouts", tmp_path / "dataset")
    first, second, third = select_cases(tmp_path / "dataset")[:3]
    cases = [first, second, third]
    out = tmp_path / "run"
    out.mkdir()
    (out / "run.json").write_text(_run_json(3), encoding="utf-8")
    append_case(out, CaseRecord(**case_fields(first[0]), status="completed"))
    append_case(out, CaseRecord(**case_fields(second[0]), status="failed"))  # e.g. a timeout

    assert [c.id for c, _ in pending(cases, out)] == [third[0].id]
    retried = pending(cases, out, retry_failed=True)
    assert [c.id for c, _ in retried] == [second[0].id, third[0].id]
