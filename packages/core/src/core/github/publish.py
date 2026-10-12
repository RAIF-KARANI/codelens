"""A finished review -> one GitHub pull request review (Phase 5 step 3b).

One `COMMENT` review per reviewed push (idea.md §9.1: never REQUEST_CHANGES;
a bot shouldn't block merges):
- **Inline comments:** one per posted finding, except on a function that
  already got a comment on an earlier push and hasn't changed since (its
  *topic*: file + function + the function's code hash, D104). The model
  words and places the same concern differently from push to push, so
  matching the finding itself would repeat it.
- **The body:** a summary, always, even when nothing new was found (the
  user's choice, D101), so authors can see the push was reviewed.

The model's text is untrusted (the code under review can steer it). GitHub
renders it as sanitised Markdown, so it can't run anything, but an @mention
in it would notify a real person. Mentions are defused before posting.
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from core.diff import FileDiff
from core.enums import FindingStatus, Severity
from core.review.pipeline import ReviewResult
from core.review.validate import CheckedFinding

_BADGE = {Severity.CRITICAL: "🔴", Severity.WARNING: "🟠", Severity.SUGGESTION: "🔵"}
_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.SUGGESTION: 2}
# "@user" or "@org/team" at a word start: what GitHub turns into a notification.
_MENTION = re.compile(r"(?<![\w`])@(?=[A-Za-z0-9])")
# GitHub caps a comment or review body at 65,536 characters.
_MAX_BODY = 60_000


@dataclass(frozen=True, slots=True)
class Publication:
    payload: dict[str, Any]  # the body of POST /repos/{o}/{r}/pulls/{n}/reviews
    new: int  # findings posted for the first time
    repeated: int  # posted findings left out: their function was already commented on
    inline: int  # of the new ones, how many sit on a diff line


def build_review(
    result: ReviewResult,
    *,
    head_sha: str,
    model: str,
    already_posted: set[str],
    inline: bool = True,
) -> Publication:
    """`inline=False` lists every new finding in the body instead: the
    fallback when GitHub refuses a comment's placement."""
    findings = sorted(
        (f for f in result.findings if f.status is FindingStatus.POSTED),
        key=lambda f: (_ORDER[f.finding.severity], f.finding.file_path, f.line_start),
    )
    new = [f for f in findings if f.topic not in already_posted]
    repeated = len(findings) - len(new)

    diffs = {change.diff.path: change.diff for change in result.changes.files}
    comments, unplaced = [], []
    for item in new:
        diff = diffs.get(item.finding.file_path)
        placement = _placement(diff, item.line_start, item.line_end) if diff and inline else None
        if placement is None:
            unplaced.append(item)  # still reported, in the body
        else:
            comments.append({"path": item.finding.file_path, **placement, "body": _comment(item)})

    body = _summary(result, head_sha, model, new, repeated, unplaced)
    payload = {"commit_id": head_sha, "event": "COMMENT", "body": body, "comments": comments}
    return Publication(payload, len(new), repeated, len(comments))


def _placement(diff: FileDiff, start: int, end: int) -> dict[str, Any] | None:
    """Where GitHub accepts the comment: on the new side (RIGHT), on a line
    shown in the diff (added or context), and a multi-line range only within
    one hunk. Validation already puts findings on added lines or their
    anchors (D82); this guards the rest."""
    for hunk in diff.hunks:
        shown = {line.new_line for line in hunk.lines if line.new_line is not None}
        if end in shown:
            if start != end and start in shown:
                return {"start_line": start, "start_side": "RIGHT", "line": end, "side": "RIGHT"}
            return {"line": end, "side": "RIGHT"}
    return None


def _comment(item: CheckedFinding) -> str:
    f = item.finding
    parts = [
        f"**{_BADGE[f.severity]} {f.severity.value}: {defuse(f.title)}** · `{f.category.value}`",
        defuse(f.explanation),
    ]
    if f.suggested_fix:
        parts.append(f"**Suggested fix:** {defuse(f.suggested_fix)}")
    parts.append(f"<sub>CodeLens · confidence {f.confidence:.2f}</sub>")
    return _cap("\n\n".join(parts))


def _summary(
    result: ReviewResult,
    head_sha: str,
    model: str,
    new: list[CheckedFinding],
    repeated: int,
    unplaced: list[CheckedFinding],
) -> str:
    reviewed = sum(1 for change in result.changes.files if change.seeds)
    lines = [f"### CodeLens review of `{head_sha[:7]}`", ""]
    lines += [f"> {defuse(' '.join(s.split()))}" for s in result.summaries if s.strip()]
    lines.append("")
    if new:
        counts = Counter(f.finding.severity for f in new)
        by_severity = " · ".join(
            f"{counts[s]} {s.value}" for s in (Severity.CRITICAL, Severity.WARNING,
                                                Severity.SUGGESTION) if counts[s]
        )  # fmt: skip
        lines.append(f"**{len(new)} new finding{'s' * (len(new) != 1)}** ({by_severity}).")
    else:
        lines.append("**No new findings** in this push.")
    if repeated:
        lines.append(
            f"{repeated} more on code already commented on in an earlier push, unchanged "
            "since (not repeated)."
        )
    if unplaced:
        lines += ["", "Findings that don't sit on a line of this diff:"]
        lines += [
            f"- {_BADGE[f.finding.severity]} `{f.finding.file_path}:{f.line_start}` "
            f"{defuse(f.finding.title)}"
            for f in unplaced
        ]
    notes = []
    if result.left_out:
        notes.append(f"{len(result.left_out)} file(s) not reviewed (size limit)")
    if result.failed_chunks:
        notes.append(f"{len(result.failed_chunks)} part(s) of the diff failed to review")
    footer = f"{reviewed} changed file(s) reviewed with `{model}`, locally"
    lines += ["", f"<sub>{'; '.join([footer, *notes])}.</sub>"]
    return _cap("\n".join(lines))


def defuse(text: str) -> str:
    """Stop "@name" in model output from notifying anyone: a zero-width space
    after the @ keeps it readable but breaks the mention."""
    return _MENTION.sub("@​", text)


def _cap(text: str) -> str:
    return text if len(text) <= _MAX_BODY else text[: _MAX_BODY - 20] + "\n\n… (truncated)"
