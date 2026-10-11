"""Scoring a run (planning.md §10.2): matching, metrics, bootstrap CIs.

Pure functions over CaseRecords: no DB, no models, so every number in a
scorecard can be recomputed from a results directory.
"""

import math
import random
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from core.enums import FindingStatus, Severity
from core.review.schemas import Category
from evals.cases import ExpectedFinding
from evals.records import CaseRecord, Prediction

LINE_TOLERANCE = 3
RECALL_AT = (5, 10, 20)
_SEVERITY_RANK = {Severity.SUGGESTION: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}

# Which predicted categories count as naming the planted bug. A reviewer who
# calls a caller-breaking change a "bug" rather than an "api-contract" problem
# has still found it; calling SQL injection "maintainability" has not.
COMPATIBLE: dict[Category, frozenset[Category]] = {
    Category.BUG: frozenset({Category.BUG, Category.API_CONTRACT, Category.ERROR_HANDLING}),
    Category.API_CONTRACT: frozenset({Category.API_CONTRACT, Category.BUG}),
    Category.CONCURRENCY: frozenset({Category.CONCURRENCY, Category.BUG}),
    Category.SECURITY: frozenset({Category.SECURITY}),
    Category.ERROR_HANDLING: frozenset(
        {Category.ERROR_HANDLING, Category.BUG, Category.API_CONTRACT}
    ),
    Category.PERFORMANCE: frozenset({Category.PERFORMANCE}),
    Category.MAINTAINABILITY: frozenset({Category.MAINTAINABILITY}),
}


def matches(expected: ExpectedFinding, predicted: Prediction) -> bool:
    """Same file, lines overlapping within ±3, a compatible category, and a
    severity at most one level below the expected one."""
    return (
        predicted.file == expected.file
        and predicted.line_start <= expected.line_end + LINE_TOLERANCE
        and predicted.line_end >= expected.line_start - LINE_TOLERANCE
        and predicted.category in COMPATIBLE[expected.category]
        and _SEVERITY_RANK[predicted.severity] >= _SEVERITY_RANK[expected.severity] - 1
    )


def match(
    expected: Sequence[ExpectedFinding], predicted: Sequence[Prediction]
) -> list[tuple[int, int]]:
    """Greedy one-to-one matching: most severe expectation first, each taking
    the most confident matching prediction still free. A second prediction of
    the same bug is a false positive (a duplicate comment is noise)."""
    pairs: list[tuple[int, int]] = []
    free = set(range(len(predicted)))
    order = sorted(range(len(expected)), key=lambda i: -_SEVERITY_RANK[expected[i].severity])
    for i in order:
        options = [j for j in sorted(free) if matches(expected[i], predicted[j])]
        if options:
            best = max(options, key=lambda j: predicted[j].confidence)
            pairs.append((i, best))
            free.remove(best)
    return sorted(pairs)


@dataclass(frozen=True, slots=True)
class CaseScore:
    kind: str  # seeded | counterfactual | clean
    tp: int
    fp: int
    fn: int
    critical_tp: int
    critical_total: int
    posted: int
    oracle_ranks: tuple[int | None, ...]  # 1-based rank of each oracle node; None = not retrieved
    oracle_packed: int  # oracle nodes that made it into the prompt
    lost: Counter[str] = field(default_factory=Counter)  # drop reason -> missed matches
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    errored: bool = False


def score_case(record: CaseRecord) -> CaseScore:
    posted = record.posted
    pairs = match(record.expected, posted)
    matched = {i for i, _ in pairs}
    critical = [i for i, e in enumerate(record.expected) if e.severity is Severity.CRITICAL]

    # Diagnostic: findings validation dropped that would have matched a miss.
    dropped = [p for p in record.findings if p.status is FindingStatus.DROPPED]
    missed = [e for i, e in enumerate(record.expected) if i not in matched]
    lost = Counter(p.drop_reason or "?" for p in dropped if any(matches(e, p) for e in missed))

    position = {(r.qualified_name, r.ordinal): k for k, r in enumerate(record.retrieved, 1)}
    included = {(r.qualified_name, r.ordinal) for r in record.retrieved if r.included}
    keys = [(o.qualified_name, o.ordinal) for o in record.oracle]
    return CaseScore(
        kind=record.kind,
        tp=len(pairs),
        fp=len(posted) - len(pairs),
        fn=len(record.expected) - len(pairs),
        critical_tp=len(matched & set(critical)),
        critical_total=len(critical),
        posted=len(posted),
        oracle_ranks=tuple(position.get(k) for k in keys),
        oracle_packed=sum(k in included for k in keys),
        lost=lost,
        seconds=record.seconds,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        errored=record.status == "error",
    )


Metrics = dict[str, float]
NAN = float("nan")


def aggregate(scores: Sequence[CaseScore]) -> Metrics:
    """The scorecard numbers. Undefined values (precision with no findings,
    retrieval with no oracle) are NaN, shown as "n/a".

    Precision counts every finding posted on a seeded *or* counterfactual case
    (D79): a counterfactual has nothing to find, so flagging it is a false
    positive. That is what lets F1, the gate metric, reward using context."""
    seeded = [s for s in scores if s.kind == "seeded"]
    counterfactual = [s for s in scores if s.kind == "counterfactual"]
    clean = [s for s in scores if s.kind == "clean"]
    tp = sum(s.tp for s in seeded)
    posted = sum(s.posted for s in seeded + counterfactual)
    expected = sum(s.tp + s.fn for s in seeded)
    precision = tp / posted if posted else NAN
    recall = tp / expected if expected else NAN
    if math.isnan(recall):
        f1 = NAN
    elif recall == 0 or math.isnan(precision):
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    critical = sum(s.critical_total for s in seeded)

    # Seeded: the context that shows the bug. Counterfactual: the context that
    # shows there is none.
    with_oracle = [s for s in seeded + counterfactual if s.oracle_ranks]
    metrics: Metrics = {
        "f1": f1,
        "precision": precision,
        "recall": recall,
        "critical_recall": sum(s.critical_tp for s in seeded) / critical if critical else NAN,
        "noise_per_clean_pr": _mean([s.posted for s in clean]),
        "false_alarms_per_counterfactual": _mean([s.posted for s in counterfactual]),
    }
    for k in RECALL_AT:
        metrics[f"oracle_recall@{k}"] = _mean(
            [
                sum(r is not None and r <= k for r in s.oracle_ranks) / len(s.oracle_ranks)
                for s in with_oracle
            ]
        )
    metrics["oracle_packed_recall"] = _mean(
        [s.oracle_packed / len(s.oracle_ranks) for s in with_oracle]
    )
    metrics["mrr"] = _mean(
        [
            1 / min((r for r in s.oracle_ranks if r is not None), default=math.inf)
            for s in with_oracle
        ]
    )
    seconds = [s.seconds for s in scores if not s.errored]
    metrics["latency_p50_s"] = percentile(seconds, 50)
    metrics["latency_p95_s"] = percentile(seconds, 95)
    metrics["input_tokens_mean"] = _mean([s.input_tokens for s in scores if not s.errored])
    metrics["output_tokens_mean"] = _mean([s.output_tokens for s in scores if not s.errored])
    return metrics


# Metrics where a bootstrap CI is meaningful (latency/tokens get point values).
QUALITY = (
    "f1",
    "precision",
    "recall",
    "critical_recall",
    "noise_per_clean_pr",
    "false_alarms_per_counterfactual",
    *(f"oracle_recall@{k}" for k in RECALL_AT),
    "oracle_packed_recall",
    "mrr",
)

Interval = tuple[float, float]


def bootstrap(
    scores: Sequence[CaseScore],
    *,
    resamples: int = 1000,
    seed: int = 0,
    statistic: Callable[[Sequence[CaseScore]], Metrics] = aggregate,
) -> dict[str, Interval]:
    """95% percentile intervals: resample the cases with replacement and
    recompute every metric. Seeded, so a scorecard is reproducible."""
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {name: [] for name in QUALITY}
    for _ in range(resamples):
        drawn = statistic(rng.choices(scores, k=len(scores)))
        for name in QUALITY:
            samples[name].append(drawn[name])
    return {name: _interval(values) for name, values in samples.items()}


def paired_bootstrap(
    candidate: dict[str, CaseScore],
    baseline: dict[str, CaseScore],
    *,
    resamples: int = 1000,
    seed: int = 0,
) -> dict[str, tuple[float, Interval]]:
    """candidate minus baseline on the cases both ran, resampling the *same* case
    ids for both (planning §10.4: compare configs paired). Returns the
    observed difference and its 95% interval per metric."""
    ids = sorted(candidate.keys() & baseline.keys())
    observed_a = aggregate([candidate[i] for i in ids])
    observed_b = aggregate([baseline[i] for i in ids])
    rng = random.Random(seed)
    deltas: dict[str, list[float]] = {name: [] for name in QUALITY}
    for _ in range(resamples):
        drawn = rng.choices(ids, k=len(ids))
        a = aggregate([candidate[i] for i in drawn])
        b = aggregate([baseline[i] for i in drawn])
        for name in QUALITY:
            deltas[name].append(a[name] - b[name])
    return {
        name: (observed_a[name] - observed_b[name], _interval(deltas[name])) for name in QUALITY
    }


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks (numpy's default)."""
    data = sorted(v for v in values if not math.isnan(v))
    if not data:
        return NAN
    position = (len(data) - 1) * q / 100
    low = math.floor(position)
    high = min(low + 1, len(data) - 1)
    return data[low] + (data[high] - data[low]) * (position - low)


def _interval(values: list[float]) -> Interval:
    return percentile(values, 2.5), percentile(values, 97.5)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else NAN
