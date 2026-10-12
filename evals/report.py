"""Scorecard for a run: JSON + markdown, optionally compared with a baseline.

    python -m evals.report evals/results/<run> [--baseline evals/results/<other>]

Writes <run>/scorecard.json and <run>/scorecard.md. With a baseline, the
deltas are paired (same cases, same resamples) and the adoption gate of
planning §10.5 is checked: F1 up with the CI's lower bound above 0, and noise
per clean PR up by no more than 0.1.
"""

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evals.metrics import (
    CaseScore,
    aggregate,
    bootstrap,
    match,
    paired_bootstrap,
    score_case,
)
from evals.records import CaseRecord, RunInfo, load_run

_GATE_MAX_NOISE_INCREASE = 0.1


def build_scorecard(
    info: RunInfo,
    records: list[CaseRecord],
    *,
    baseline: tuple[RunInfo, list[CaseRecord]] | None = None,
    resamples: int = 1000,
) -> dict[str, Any]:
    scores = {r.case_id: score_case(r) for r in records}
    values = aggregate(list(scores.values()))
    intervals = bootstrap(list(scores.values()), resamples=resamples)
    lost: Counter[str] = Counter()
    for s in scores.values():
        lost.update(s.lost)

    card: dict[str, Any] = {
        "run": info.name,
        "config": info.config.get("name"),
        "llm_model": info.llm_model,
        "model_digests": info.model_digests,
        "context_window": info.context_window,
        "cases": {
            "seeded": sum(r.kind == "seeded" for r in records),
            "counterfactual": sum(r.kind == "counterfactual" for r in records),
            "clean": sum(r.kind == "clean" for r in records),
            "planned": info.cases,
            "errors": sum(r.status == "error" for r in records),
            "failed_reviews": sum(r.status == "failed" for r in records),
            "think_fallbacks": sum(r.fallback_chunks for r in records),
        },
        "metrics": {
            name: {"value": _num(v), "ci95": _pair(intervals[name]) if name in intervals else None}
            for name, v in values.items()
        },
        "by_operator": _by_operator(records, scores),
        "lost_to_validation": dict(sorted(lost.items())),
        "anchored": _anchored(records),
    }
    if baseline is not None:
        base_info, base_records = baseline
        base_scores = {r.case_id: score_case(r) for r in base_records}
        deltas = paired_bootstrap(scores, base_scores, resamples=resamples)
        f1_delta, f1_ci = deltas["f1"]
        noise_delta, _ = deltas["noise_per_clean_pr"]
        card["baseline"] = {
            "run": base_info.name,
            "config": base_info.config.get("name"),
            "paired_cases": len(scores.keys() & base_scores.keys()),
            "deltas": {
                name: {"delta": _num(d), "ci95": _pair(ci)} for name, (d, ci) in deltas.items()
            },
            "gate": {
                "f1_up_with_ci_above_0": f1_delta > 0 and f1_ci[0] > 0,
                "noise_increase_at_most_0.1": not noise_delta > _GATE_MAX_NOISE_INCREASE,
                "latency": "check p95 against the budget by hand (planning 10.5)",
            },
        }
    return card


def _anchored(records: list[CaseRecord]) -> dict[str, int]:
    """Posted findings validation moved onto an anchor, and how many of those
    matched a planted bug (the rest are false positives it let through)."""
    posted = matched = 0
    for record in records:
        found = record.posted
        hits = {j for _, j in match(record.expected, found)}
        for j, prediction in enumerate(found):
            if prediction.anchored_from is not None:
                posted += 1
                matched += j in hits
    return {"posted": posted, "matched": matched}


def _by_operator(records: list[CaseRecord], scores: dict[str, CaseScore]) -> dict[str, Any]:
    groups: dict[str, list[CaseScore]] = defaultdict(list)
    for r in records:
        groups[r.operator].append(scores[r.case_id])
    table = {}
    for operator, group in sorted(groups.items()):
        m = aggregate(group)
        kind = group[0].kind
        seeded = kind == "seeded"
        alarms = (
            m["noise_per_clean_pr"] if kind == "clean" else m["false_alarms_per_counterfactual"]
        )
        table[operator] = {
            "kind": kind,
            "cases": len(group),
            "tp": sum(s.tp for s in group),
            "posted": sum(s.posted for s in group),
            "recall": _num(m["recall"]) if seeded else None,
            "noise_per_pr": None if seeded else _num(alarms),
            "oracle_recall@10": None if kind == "clean" else _num(m["oracle_recall@10"]),
        }
    return table


def render_markdown(card: dict[str, Any]) -> str:
    cases = card["cases"]
    lines = [
        f"# Scorecard: {card['run']}",
        "",
        f"config **{card['config']}**, model `{card['llm_model']}`, "
        f"window {card['context_window']}, {cases['seeded']} seeded + "
        f"{cases.get('counterfactual', 0)} counterfactual + {cases['clean']} clean "
        f"cases (of {cases['planned']} planned; {cases['errors']} errors, "
        f"{cases['failed_reviews']} failed reviews, "
        f"{cases.get('think_fallbacks', 0)} thinking fallbacks)",
        "",
    ]
    baseline = card.get("baseline")
    header = "| metric | value | 95% CI |"
    rule = "|---|---|---|"
    if baseline:
        header += f" delta vs {baseline['config']} | delta 95% CI (paired) |"
        rule += "---|---|"
    lines += [header, rule]
    for name, metric in card["metrics"].items():
        row = f"| {name} | {_fmt(metric['value'])} | {_fmt_ci(metric['ci95'])} |"
        if baseline:
            delta = baseline["deltas"].get(name)
            row += (
                f" {_fmt(delta['delta'], signed=True)} | {_fmt_ci(delta['ci95'])} |"
                if delta
                else " | |"
            )
        lines.append(row)

    lines += [
        "",
        "## By operator",
        "",
        "| operator | kind | cases | tp / posted | recall | noise/PR | oracle@10 |",
        "|---|---|---|---|---|---|---|",
    ]
    for operator, row in card["by_operator"].items():
        cells = [
            operator,
            row["kind"],
            str(row["cases"]),
            f"{row['tp']} / {row['posted']}",
            _fmt(row["recall"]),
            _fmt(row["noise_per_pr"]),
            _fmt(row["oracle_recall@10"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lost = card["lost_to_validation"]
    lines += [
        "",
        "Matches lost to validation (dropped findings that would have matched a miss): "
        + (", ".join(f"{k} {v}" for k, v in lost.items()) or "none"),
    ]
    anchored = card.get("anchored", {})
    if anchored.get("posted"):
        lines.append(
            f"Findings moved by anchoring: {anchored['posted']} posted, "
            f"{anchored['matched']} of them matched a planted bug."
        )
    if baseline:
        gate = baseline["gate"]
        lines += [
            "",
            f"## Adoption gate vs {baseline['run']} ({baseline['paired_cases']} paired cases)",
            "",
            f"- F1 up, CI lower bound > 0: {_yes(gate['f1_up_with_ci_above_0'])}",
            f"- Noise per clean PR up by at most 0.1: {_yes(gate['noise_increase_at_most_0.1'])}",
            f"- Latency: {gate['latency']}",
        ]
    return "\n".join(lines) + "\n"


def write_scorecard(
    directory: Path,
    *,
    baseline: Path | None = None,
    resamples: int = 1000,
    completed_only: bool = False,
) -> Path:
    """Score a run (optionally against a baseline) and write scorecard.{json,md}.

    completed_only: keep only cases every run involved reviewed successfully.
    A failed review posts nothing, which on a clean or counterfactual case
    looks like a correct "no finding"; this view removes that flattery. It is
    written as scorecard-completed.{json,md} beside the full one."""
    info, records = load_run(directory)
    base = load_run(baseline) if baseline else None
    if completed_only:
        keep = {r.case_id for r in records if r.status == "completed"}
        if base is not None:
            keep &= {r.case_id for r in base[1] if r.status == "completed"}
            base = (base[0], [r for r in base[1] if r.case_id in keep])
        records = [r for r in records if r.case_id in keep]
    card = build_scorecard(info, records, baseline=base, resamples=resamples)
    stem = "scorecard-completed" if completed_only else "scorecard"
    (directory / f"{stem}.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    path = directory / f"{stem}.md"
    path.write_text(render_markdown(card), encoding="utf-8", newline="\n")
    return path


def _num(value: float) -> float | None:
    """NaN isn't valid JSON: undefined metrics become null."""
    return None if value is None or math.isnan(value) else round(value, 4)


def _pair(interval: tuple[float, float]) -> list[float | None]:
    return [_num(interval[0]), _num(interval[1])]


def _fmt(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.3f}" if signed else f"{value:.3f}"


def _yes(passed: bool) -> str:
    return "yes" if passed else "no"


def _fmt_ci(ci: list[float | None] | None) -> str:
    if not ci or ci[0] is None:
        return ""
    return f"[{_fmt(ci[0])}, {_fmt(ci[1])}]"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m evals.report", description=__doc__.split("\n")[0]
    )
    parser.add_argument("run", type=Path, help="results directory of the run")
    parser.add_argument("--baseline", type=Path, help="results directory to compare against")
    parser.add_argument("--resamples", type=int, default=1000)
    parser.add_argument(
        "--completed-only",
        action="store_true",
        help="only cases every run reviewed successfully (failed reviews post nothing)",
    )
    args = parser.parse_args()
    path = write_scorecard(
        args.run,
        baseline=args.baseline,
        resamples=args.resamples,
        completed_only=args.completed_only,
    )
    print(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
