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
in it would notify a real person, under the App's name. Mentions are defused
before posting, and a file path (which a fork PR's author picks) goes in a
code span its own backticks can't close.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from core.diff import FileDiff
from core.diff.git import GitError
from core.enums import FindingStatus, Severity
from core.github.client import GitHubError
from core.providers.base import ProviderUnavailableError, StructuredOutputError
from core.review.pipeline import ReviewResult
from core.review.validate import CheckedFinding

_BADGE = {Severity.CRITICAL: "🔴", Severity.WARNING: "🟠", Severity.SUGGESTION: "🔵"}
_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.SUGGESTION: 2}
# "@user" or "@org/team": what GitHub turns into a notification. Every such
# @ is defused, wherever it is, except right after an ASCII letter or digit
# (an e-mail address). Exempting code spans by the character before the @
# (D133) let "`x`@user", "é@user" and "_@user" through: a backtick can close
# a span as well as open one, and Python's \w is wider than GitHub's.
_MENTION = re.compile(r"(?<![A-Za-z0-9])@(?=[A-Za-z0-9])")
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
    """Where GitHub accepts the comment: on the new side (RIGHT), on lines
    shown in the diff (added or context), and a range only within one hunk.

    The finding's range is clipped to the first hunk it overlaps. Measured on
    a real PR: the model flagged a swapped parameter as lines 2263-2321 (the
    whole function), around a 6-line hunk. Requiring the range's own ends to
    be in the hunk sent two of four true findings to the review body."""
    for hunk in diff.hunks:
        inside = [
            line.new_line
            for line in hunk.lines
            if line.new_line is not None and start <= line.new_line <= end
        ]
        if inside:
            first, last = inside[0], inside[-1]  # a hunk's new lines are consecutive
            if first == last:
                return {"line": last, "side": "RIGHT"}
            return {"start_line": first, "start_side": "RIGHT", "line": last, "side": "RIGHT"}
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
            f"- {_BADGE[f.finding.severity]} {_code(f'{f.finding.file_path}:{f.line_start}')} "
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


# Why a review failed, as the pull request is told (Phase 11 step 4): a fixed
# list, so nothing from inside (paths, hosts, stack traces) reaches GitHub.
# The whole error stays in the dashboard and the logs.
TOO_LONG = "it took longer than its time limit"
NO_MODEL = "the model server didn't answer"
UNUSABLE = "the model's answer couldn't be used"
NO_CODE = "the pull request's code couldn't be fetched from GitHub"
TOO_BIG = "the change is too large for the model's context window"
NO_PART = "the model couldn't review any part of it"
INTERNAL = "an internal error stopped it"


def failure_reason(
    error: BaseException | None,
    failed_chunks: Sequence[tuple[int, str]] = (),
    *,
    timed_out: bool = False,
) -> str:
    """Which of the reasons above: from the exception that ended the review,
    or, when the pipeline itself returned `failed` (every chunk failed), from
    what the chunks said."""
    if timed_out:
        return TOO_LONG
    if isinstance(error, ProviderUnavailableError):
        return NO_MODEL
    if isinstance(error, StructuredOutputError):
        return UNUSABLE
    if error is not None and any(
        isinstance(e, (GitHubError, GitError)) for e in (error, error.__cause__)
    ):
        return NO_CODE
    if error is None and failed_chunks:
        if all("context window" in message for _, message in failed_chunks):
            return TOO_BIG
        return NO_PART
    return INTERNAL


def build_failure_note(head_sha: str, reason: str) -> dict[str, Any]:
    """A `COMMENT` review that only says the push wasn't reviewed, and why.
    No `commit_id`: the head may be one GitHub can't place (a failed fetch),
    and the body names the commit anyway."""
    retry = (
        "A smaller pull request fits."
        if reason == TOO_BIG
        else "Push again, or close and reopen the pull request, to retry."
    )
    body = "\n".join(
        [
            f"### CodeLens couldn't review `{head_sha[:7]}`",
            "",
            f"{reason[0].upper()}{reason[1:]}. {retry}",
            "",
            "<sub>Nothing else was posted for this push. Whoever runs this CodeLens can see "
            "the details on its dashboard.</sub>",
        ]
    )
    return {"event": "COMMENT", "body": body}


def defuse(text: str) -> str:
    """Stop "@name" in model output from notifying anyone: a zero-width space
    after the @ keeps it readable but breaks the mention."""
    return _MENTION.sub("@​", text)


def _code(text: str) -> str:
    """`text` as one inline code span, whatever backticks it holds: fenced by
    a longer run of backticks than any inside it (CommonMark), on one line,
    and defused too (a zero-width space in code is harmless)."""
    text = " ".join(text.splitlines())
    fence = "`" * (max(map(len, re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{defuse(text)}{pad}{fence}"


def _cap(text: str) -> str:
    return text if len(text) <= _MAX_BODY else text[: _MAX_BODY - 20] + "\n\n… (truncated)"
