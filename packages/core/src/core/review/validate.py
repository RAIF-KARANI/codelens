"""S9 validate: the hallucination guard. Every finding must pass, in order:

  path_not_in_chunk      the file isn't one this chunk reviewed
  not_on_changed_lines   its lines don't touch any line the diff added
                         (with anchoring on: nor any anchor within the window)
  unknown_evidence       it cites an id the model was never shown
  low_confidence         below config.min_confidence
  duplicate              same fingerprint as a higher-confidence finding
  over_cap               beyond config.max_comments (severity, then confidence)
  verified_safe          (later, in verify.py) its callers show it can't happen

Dropped findings are kept with their reason, for traces and evals.

Anchoring (config.anchor_window / anchor_deletions, planning Q11): a finding
about the change that the model placed a few lines off is moved onto the
nearest anchor instead of being dropped. Anchors are the added lines, plus,
with anchor_deletions, the lines bordering a pure deletion (which has no
added line to comment on). The original lines are kept in `anchored_from`.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from core.enums import FindingStatus, Severity
from core.review.config import ValidationConfig
from core.review.packing import ReviewChunk
from core.review.schemas import FindingOut

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.SUGGESTION: 2}


@dataclass(frozen=True, slots=True)
class CheckedFinding:
    finding: FindingOut
    chunk_index: int
    status: FindingStatus
    drop_reason: str | None
    evidence_node_ids: tuple[int, ...]  # "N12" -> 12; "C" labels aren't stored nodes
    fingerprint: str
    line_start: int  # normalised (start <= end); after anchoring, the anchor
    line_end: int
    anchored_from: tuple[int, int] | None = None  # the model's lines, if moved


def validate(
    raw: Sequence[tuple[ReviewChunk, Sequence[FindingOut]]], config: ValidationConfig
) -> list[CheckedFinding]:
    checked: list[CheckedFinding] = []
    for chunk, findings in raw:
        for finding in findings:
            checked.append(_check_one(chunk, finding, config))

    # Duplicates: keep the most confident of each fingerprint.
    best: dict[str, CheckedFinding] = {}
    for item in checked:
        if item.status is FindingStatus.POSTED:
            current = best.get(item.fingerprint)
            if current is None or item.finding.confidence > current.finding.confidence:
                best[item.fingerprint] = item
    checked = [
        item
        if item.status is not FindingStatus.POSTED or best[item.fingerprint] is item
        else drop(item, "duplicate")
        for item in checked
    ]

    # Cap: most severe first, then most confident.
    posted = sorted(
        (i for i in checked if i.status is FindingStatus.POSTED),
        key=lambda i: (_SEVERITY_ORDER[i.finding.severity], -i.finding.confidence),
    )
    over = {id(i) for i in posted[config.max_comments :]}
    return [drop(i, "over_cap") if id(i) in over else i for i in checked]


def _check_one(chunk: ReviewChunk, finding: FindingOut, config: ValidationConfig) -> CheckedFinding:
    start, end = sorted((finding.line_start, finding.line_end))
    change = chunk.file(finding.file_path)
    evidence_ids = tuple(int(e[1:]) for e in finding.evidence if e[:1] == "N" and e[1:].isdigit())
    moved: tuple[int, int] | None = None

    def result(reason: str | None) -> CheckedFinding:
        status = FindingStatus.DROPPED if reason else FindingStatus.POSTED
        fingerprint = _fingerprint(chunk, finding, start, end)
        return CheckedFinding(
            finding, chunk.index, status, reason, evidence_ids, fingerprint, start, end, moved
        )

    if change is None:
        return result("path_not_in_chunk")
    anchors = set(change.diff.added_lines())
    if config.anchor_deletions:
        anchors |= change.diff.deletion_borders()
    if not any(start <= line <= end for line in anchors):
        nearest = _nearest(anchors, start, end, config.anchor_window)
        if nearest is None:
            return result("not_on_changed_lines")
        moved, start, end = (start, end), nearest, nearest
    if any(e not in chunk.labels for e in finding.evidence):
        return result("unknown_evidence")
    if finding.confidence < config.min_confidence:
        return result("low_confidence")
    return result(None)


def _nearest(anchors: set[int], start: int, end: int, window: int) -> int | None:
    """The anchor closest to [start, end] (the earlier one on a tie), if it is
    at most `window` lines away."""
    if window <= 0 or not anchors:
        return None

    def distance(line: int) -> int:
        return start - line if line < start else line - end

    best = min(anchors, key=lambda line: (distance(line), line))
    return best if distance(best) <= window else None


def drop(item: CheckedFinding, reason: str) -> CheckedFinding:
    """The same finding, dropped for `reason`."""
    return CheckedFinding(
        item.finding,
        item.chunk_index,
        FindingStatus.DROPPED,
        reason,
        item.evidence_node_ids,
        item.fingerprint,
        item.line_start,
        item.line_end,
        item.anchored_from,
    )


def _fingerprint(chunk: ReviewChunk, finding: FindingOut, start: int, end: int) -> str:
    """Identity of a finding that survives line shifts: the file, category, and
    the (whitespace-normalised) changed code it points at."""
    code = ""
    change = chunk.file(finding.file_path)
    if change is not None and change.head is not None:
        lines = change.head.module_node.code.split("\n")
        code = " ".join(" ".join(lines[start - 1 : end]).split())
    key = f"{finding.file_path}|{finding.category.value}|{code}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
