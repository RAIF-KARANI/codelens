"""Matching and metrics on hand-made records: every rule of planning §10.2."""

import json
import math
from pathlib import Path
from typing import Any

import pytest

from evals.cases import ExpectedFinding, OracleNode
from evals.metrics import (
    aggregate,
    bootstrap,
    match,
    matches,
    paired_bootstrap,
    percentile,
    score_case,
)
from evals.records import CaseRecord, Prediction, Retrieved, RunInfo, append_case, write_run
from evals.report import build_scorecard, render_markdown, write_scorecard

EXPECTED = ExpectedFinding(
    file="a.py", line_start=10, line_end=10, category="api-contract", severity="critical"
)


def pred(**overrides: Any) -> Prediction:
    values: dict[str, Any] = {
        "file": "a.py",
        "line_start": 10,
        "line_end": 10,
        "category": "api-contract",
        "severity": "critical",
        "confidence": 0.8,
        "title": "t",
        "status": "posted",
    }
    return Prediction.model_validate({**values, **overrides})


def record(case_id: str, findings: list[Prediction], **overrides: Any) -> CaseRecord:
    values: dict[str, Any] = {
        "case_id": case_id,
        "kind": "seeded",
        "split": "test",
        "operator": "drop_param",
        "source": "s",
        "expected": [EXPECTED],
        "oracle": [OracleNode(qualified_name="m.caller", role="caller")],
        "status": "completed",
        "findings": findings,
    }
    return CaseRecord.model_validate({**values, **overrides})


def clean(case_id: str, posted: int) -> CaseRecord:
    return record(
        case_id,
        [pred() for _ in range(posted)],
        kind="clean",
        operator="rename_local",
        expected=[],
        oracle=[],
    )


# ---------------------------------------------------------------- matching


@pytest.mark.parametrize(
    ("overrides", "ok"),
    [
        ({}, True),
        ({"line_start": 13, "line_end": 14}, True),  # within ±3
        ({"line_start": 14, "line_end": 14}, False),
        ({"line_start": 2, "line_end": 7}, True),  # 7 is within 3 of 10
        ({"file": "b.py"}, False),
        ({"category": "bug"}, True),  # compatible with api-contract
        ({"category": "maintainability"}, False),
        ({"severity": "warning"}, True),  # one level below
        ({"severity": "suggestion"}, False),
    ],
)
def test_matching_rule(overrides: dict[str, Any], ok: bool) -> None:
    assert matches(EXPECTED, pred(**overrides)) is ok


def test_security_needs_security() -> None:
    sql = EXPECTED.model_copy(update={"category": "security"})
    assert not matches(sql, pred(category="bug"))
    assert matches(sql, pred(category="security"))


def test_matching_is_one_to_one_and_prefers_confidence() -> None:
    found = [pred(confidence=0.7), pred(confidence=0.9), pred(file="b.py")]

    assert match([EXPECTED], found) == [(0, 1)]
    score = score_case(record("c", found))
    assert (score.tp, score.fp, score.fn) == (1, 2, 0)  # the duplicate is noise too


def test_dropped_findings_that_would_have_matched_are_reported() -> None:
    dropped = pred(status="dropped", drop_reason="not_on_changed_lines")

    score = score_case(record("c", [dropped, pred(file="b.py")]))

    assert (score.tp, score.fp, score.fn) == (0, 1, 1)
    assert score.lost == {"not_on_changed_lines": 1}


# ---------------------------------------------------------------- metrics


def test_aggregate_metrics() -> None:
    retrieved = [
        Retrieved(qualified_name="m.other", ordinal=0, similarity=0.9, included=True),
        Retrieved(qualified_name="m.caller", ordinal=0, similarity=0.8, included=False),
    ]
    records = [
        record("hit", [pred()], retrieved=retrieved, seconds=10, input_tokens=100),
        record("miss", [pred(file="b.py")], seconds=30, input_tokens=300),
        clean("clean1", 0),
        clean("clean2", 1),
    ]

    m = aggregate([score_case(r) for r in records])

    assert m["precision"] == 0.5 and m["recall"] == 0.5 and m["f1"] == 0.5
    assert m["critical_recall"] == 0.5
    assert m["noise_per_clean_pr"] == 0.5
    # "hit" found the oracle at rank 2 but didn't pack it; "miss" found nothing.
    assert m["oracle_recall@5"] == 0.5 and m["mrr"] == 0.25
    assert m["oracle_packed_recall"] == 0.0
    assert m["latency_p50_s"] == 5.0  # clean cases count too: median of [0, 0, 10, 30]
    assert m["input_tokens_mean"] == 100


def test_undefined_metrics_are_nan_not_zero() -> None:
    m = aggregate([score_case(record("quiet", []))])

    assert math.isnan(m["precision"])  # no findings: precision is undefined
    assert m["recall"] == 0 and m["f1"] == 0
    assert math.isnan(m["noise_per_clean_pr"])  # no clean cases


def test_percentile_interpolates() -> None:
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([5], 95) == 5
    assert math.isnan(percentile([], 50))


def test_bootstrap_is_reproducible_and_brackets_the_estimate() -> None:
    scores = [score_case(record(f"c{i}", [pred()] if i % 3 else [])) for i in range(30)]

    first = bootstrap(scores, resamples=300, seed=1)
    again = bootstrap(scores, resamples=300, seed=1)

    assert first == again
    low, high = first["recall"]
    assert low <= aggregate(scores)["recall"] <= high
    assert low < high


def test_paired_bootstrap_of_a_run_against_itself_is_exactly_zero() -> None:
    scores = {f"c{i}": score_case(record(f"c{i}", [pred()] if i % 2 else [])) for i in range(20)}

    deltas = paired_bootstrap(scores, scores, resamples=100)

    assert deltas["f1"] == (0.0, (0.0, 0.0))


# ---------------------------------------------------------------- scorecard


def test_scorecard_is_strict_json_and_renders() -> None:
    info = RunInfo(
        name="r",
        config={"name": "b0"},
        llm_model="m",
        embed_model="e",
        model_digests={"m": "fake"},
        context_window=8192,
        temperature=0.1,
        seed=42,
        codelens_commit=None,
        dataset_hash="h",
        cases=3,
        filters={},
        started_at="now",
    )
    better = [record("a", [pred()]), record("b", [pred()]), clean("c", 0)]
    worse = [record("a", []), record("b", []), clean("c", 0)]

    card = build_scorecard(info, better, baseline=(info, worse), resamples=50)

    json.dumps(card, allow_nan=False)  # NaN would be invalid JSON
    assert card["metrics"]["precision"]["value"] == 1.0
    assert card["baseline"]["deltas"]["recall"]["delta"] == 1.0
    assert card["baseline"]["gate"]["f1_up_with_ci_above_0"] is True
    text = render_markdown(card)
    assert "| f1 | 1.000 |" in text and "drop_param" in text
    assert text.isascii()  # Windows consoles print it


def test_completed_only_view_drops_cases_a_run_failed(tmp_path: Path) -> None:
    """A failed review posts nothing, which on a counterfactual looks like a
    correct "no alarm". The completed-only view must not count it."""
    info = RunInfo(
        name="r",
        config={"name": "x"},
        llm_model="m",
        embed_model="e",
        model_digests={},
        context_window=8192,
        temperature=0.1,
        seed=42,
        codelens_commit=None,
        dataset_hash="h",
        cases=2,
        filters={},
        started_at="now",
    )
    safe = {"kind": "counterfactual", "operator": "drop_param_cf", "expected": []}
    runs = {
        "candidate": [record("bug", [pred()]), record("safe", [], status="failed", **safe)],
        "baseline": [record("bug", [pred()]), record("safe", [pred()], **safe)],
    }
    for name, records in runs.items():
        write_run(tmp_path / name, info)
        for r in records:
            append_case(tmp_path / name, r)

    write_scorecard(tmp_path / "candidate", baseline=tmp_path / "baseline", resamples=20)
    write_scorecard(
        tmp_path / "candidate", baseline=tmp_path / "baseline", resamples=20, completed_only=True
    )

    full = json.loads((tmp_path / "candidate" / "scorecard.json").read_text(encoding="utf-8"))
    kept = json.loads(
        (tmp_path / "candidate" / "scorecard-completed.json").read_text(encoding="utf-8")
    )
    # Full view: the failure "wins" the counterfactual. Completed-only: it's gone.
    assert full["baseline"]["deltas"]["false_alarms_per_counterfactual"]["delta"] == -1.0
    assert full["cases"]["failed_reviews"] == 1
    assert kept["baseline"]["paired_cases"] == 1 and kept["cases"]["counterfactual"] == 0
