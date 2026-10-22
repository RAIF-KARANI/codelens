"""Turning a review into a GitHub review: placement, repeats, the summary,
and defused @mentions. Pure functions; no database or GitHub."""

import html
import re
from dataclasses import replace
from typing import Any

import pytest

from core.diff import parse_unified_diff
from core.diff.git import GitError
from core.enums import FindingStatus, Severity
from core.github import publish
from core.github.client import GitHubError
from core.github.publish import Publication, _code, build_review, defuse
from core.providers import Usage
from core.providers.base import ProviderUnavailableError, StructuredOutputError
from core.review.changes import ChangeSet, FileChange
from core.review.pipeline import ReviewResult
from core.review.schemas import Category, FindingOut
from core.review.validate import CheckedFinding

DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,4 +1,5 @@
 def a():
-    return 1
+    x = 1
+    return x


@@ -20,2 +21,3 @@ def b():
     y = 2
+    z = 3
     return y
"""


def finding(
    start: int,
    end: int,
    *,
    topic: str = "t",
    severity: Severity = Severity.WARNING,
    status: FindingStatus = FindingStatus.POSTED,
    title: str = "x is shadowed",
    path: str = "app.py",
) -> CheckedFinding:
    out = FindingOut(
        file_path=path,
        line_start=start,
        line_end=end,
        severity=severity,
        category=Category.BUG,
        title=title,
        explanation="Callers break, says @octocat.",
        suggested_fix="Rename it.",
        confidence=0.8,
    )
    return CheckedFinding(out, 0, status, None, (), "fp", start, end, topic=topic)


def result(*findings: CheckedFinding, summaries: tuple[str, ...] = ("Adds x.",)) -> ReviewResult:
    [diff] = parse_unified_diff(DIFF)
    changes = ChangeSet("b" * 40, "h" * 40, (FileChange(diff, None, None, ()),))
    return ReviewResult(
        review_id=1,
        status=None,  # type: ignore[arg-type]  # not read by build_review
        changes=changes,
        chunks=[],
        left_out=[],
        summaries=list(summaries),
        findings=list(findings),
        failed_chunks=[],
        usage=Usage(),
    )


def build(
    *findings: CheckedFinding, already: set[str] | None = None, inline: bool = True
) -> Publication:
    return build_review(
        result(*findings),
        head_sha="h" * 40,
        model="qwen3:8b",
        already_posted=already or set(),
        inline=inline,
    )


def test_comments_sit_on_lines_github_accepts() -> None:
    pub = build(
        finding(2, 3, topic="multi"),  # both lines in hunk 1: a range
        finding(3, 22, topic="span"),  # spans two hunks: clipped to the first
        # Wider than hunk 2 on both sides, like the model's "lines 2263-2321"
        # around a 6-line hunk on a real PR: clipped to the hunk.
        finding(15, 30, topic="wide"),
        finding(22, 22, topic="single"),  # added line in hunk 2
    )
    placements = [
        {k: v for k, v in c.items() if k not in ("path", "body")} for c in pub.payload["comments"]
    ]
    assert placements == [
        {"start_line": 2, "start_side": "RIGHT", "line": 3, "side": "RIGHT"},
        {"start_line": 3, "start_side": "RIGHT", "line": 5, "side": "RIGHT"},
        {"start_line": 21, "start_side": "RIGHT", "line": 23, "side": "RIGHT"},
        {"line": 22, "side": "RIGHT"},
    ]
    assert pub.payload["event"] == "COMMENT"
    assert pub.payload["commit_id"] == "h" * 40


def test_a_finding_off_the_diff_is_listed_in_the_body() -> None:
    pub = build(finding(10, 10, title="far away"))  # line 10 is in no hunk

    assert pub.payload["comments"] == []
    assert (pub.new, pub.inline) == (1, 0)
    assert "- 🟠 `app.py:10` far away" in pub.payload["body"]


def test_repeats_are_left_out_but_counted() -> None:
    pub = build(
        finding(2, 2, topic="seen"),
        finding(22, 22, topic="fresh", severity=Severity.CRITICAL),
        finding(3, 3, topic="dropped", status=FindingStatus.DROPPED),
        already={"seen"},
    )

    assert (pub.new, pub.repeated, pub.inline) == (1, 1, 1)
    assert pub.payload["comments"][0]["line"] == 22
    body = pub.payload["body"]
    assert "**1 new finding** (1 critical)." in body
    assert "1 more on code already commented on in an earlier push, unchanged since" in body


def test_a_push_with_nothing_new_still_gets_a_summary() -> None:
    pub = build(finding(2, 2, topic="seen"), already={"seen"})

    assert pub.payload["comments"] == []
    assert "### CodeLens review of `hhhhhhh`" in pub.payload["body"]
    assert "**No new findings** in this push." in pub.payload["body"]
    assert "> Adds x." in pub.payload["body"]


def test_inline_false_puts_everything_in_the_body() -> None:
    pub = build(finding(2, 3), inline=False)

    assert pub.payload["comments"] == []
    assert "`app.py:2` x is shadowed" in pub.payload["body"]


def test_mentions_in_model_output_are_defused() -> None:
    pub = build(finding(2, 2, title="ping @admin"))
    comment = pub.payload["comments"][0]["body"]

    assert "@octocat" not in comment and "@​octocat" in comment
    assert "@admin" not in comment
    # Not a mention: an e-mail address stays as it is.
    assert defuse("mail a@b.com") == "mail a@b.com"
    assert defuse("@org/team and (@user)") == "@​org/team and (@​user)"
    # In code too: the zero-width space can't be seen, and code-span edges
    # are what the old exemption got wrong.
    assert defuse("`@decorator`") == "`@\u200bdecorator`"


# Phase 9 step 7 (D133): the old pattern skipped any @ after a backtick or a
# Python word character, and the body listed a finding's file path raw.
BYPASSES = ["`x`@u", "a`@u", "\u00e9@u", "\u0394@u", "_@u", "path `a` @u `b`"]


def _live_mentions(pub: Publication) -> list[str]:
    texts = [pub.payload["body"], *(c["body"] for c in pub.payload["comments"])]
    return [m for text in texts for m in re.findall(r"(?<![A-Za-z0-9])@[A-Za-z0-9]+", text)]


@pytest.mark.parametrize("inline", [True, False])
@pytest.mark.parametrize("payload", BYPASSES)
def test_no_mention_survives_in_any_text(payload: str, inline: bool) -> None:
    item = finding(2, 2, title=f"t {payload}")
    item = replace(item, finding=item.finding.model_copy(update={"explanation": payload}))
    pub = build_review(
        result(item, summaries=(f"summary {payload}",)),
        head_sha="h" * 40,
        model="qwen3:8b",
        already_posted=set(),
        inline=inline,
    )
    assert _live_mentions(pub) == []


# Security audit run-2 (D187): GitHub's Markdown decodes character references
# in text, so these would read "@octocat" and "@org/team" once rendered.
ENTITY_MENTIONS = [
    "&#64;octocat",
    "&#x40;octocat",
    "&#X40;octocat",
    "&commat;octocat",
    "@&#111;ctocat",
    "&#64;org/team",
    "cc &#64;a and &#x40;b",
]


@pytest.mark.parametrize("payload", ENTITY_MENTIONS)
def test_no_character_reference_decodes_into_a_mention(payload: str) -> None:
    # What GitHub shows is the text with references decoded once: "&amp;" back
    # to "&", the rest left literal. No live @mention may come out of it.
    shown = html.unescape(defuse(payload))
    assert re.findall(r"(?<![A-Za-z0-9])@[A-Za-z0-9]", shown) == []


def test_text_reads_the_same_apart_from_mentions() -> None:
    # Escaping an & that only looks like a reference ("AT&T;") changes the
    # Markdown, not what it shows.
    plain = "a & b, x&y, AT&T; 3 < 4"
    assert html.unescape(defuse(plain)) == plain
    assert defuse("ping @admin &#64;admin") == "ping @​admin &amp;#64;admin"


def test_a_code_span_keeps_references_as_typed() -> None:
    # Code spans never decode references, so they are left alone there.
    assert _code("a&#64;b.py") == "`a&#64;b.py`"


def test_a_file_path_cant_close_its_code_span() -> None:
    # A fork PR names the file; a finding on it is listed in the body when
    # GitHub refuses inline comments (the 422 fallback).
    path = "src/a` @victim `b.py"
    pub = build(finding(2, 2, path=path), inline=False)

    body = pub.payload["body"]
    assert "- \U0001f7e0 ``src/a` @\u200bvictim `b.py:2`` x is shadowed" in body
    assert _live_mentions(pub) == []
    assert _code("``x``") == "``` ``x`` ```"  # padded when it starts or ends with one


# --- a failed review, said on the pull request (Phase 11 step 4)


def _caused_by(cause: BaseException) -> Exception:
    """A ReviewError-like wrapper, as the worker raises around a failed fetch."""
    try:
        raise RuntimeError("octo/shop#2: fetch failed") from cause
    except RuntimeError as wrapped:
        return wrapped


@pytest.mark.parametrize(
    ("error", "chunks", "timed_out", "reason"),
    [
        (RuntimeError("x"), (), True, publish.TOO_LONG),
        (ProviderUnavailableError("http://host.docker.internal:11434 refused"), (), False,
         publish.NO_MODEL),
        (StructuredOutputError("bad json", raw="{"), (), False, publish.UNUSABLE),
        (GitHubError(404, "Not Found", method="GET", path="/repositories/1"), (), False,
         publish.NO_CODE),
        (_caused_by(GitError("fatal: couldn't find remote ref")), (), False, publish.NO_CODE),
        (None, [(0, "the diff alone exceeds the 8192-token context window")], False,
         publish.TOO_BIG),
        (None, [(0, "the diff alone exceeds the 8192-token context window"), (1, "timed out")],
         False, publish.NO_PART),
        (KeyError("anything else"), (), False, publish.INTERNAL),
    ],
    ids=["timeout", "no-model", "unusable", "github", "git", "too-big", "chunks", "other"],
)  # fmt: skip
def test_a_failure_gets_one_of_the_fixed_reasons(
    error: BaseException | None, chunks: Any, timed_out: bool, reason: str
) -> None:
    assert publish.failure_reason(error, chunks, timed_out=timed_out) == reason


def test_the_failure_note_says_why_and_how_to_retry_and_nothing_else() -> None:
    note = publish.build_failure_note("abc1234" + "0" * 33, publish.NO_MODEL)
    assert note["event"] == "COMMENT"
    assert "commit_id" not in note  # the head may be one GitHub can't place
    assert note["body"].startswith("### CodeLens couldn't review `abc1234`")
    assert "The model server didn't answer. Push again, or close and reopen" in note["body"]
    too_big = publish.build_failure_note("a" * 40, publish.TOO_BIG)["body"]
    assert "A smaller pull request fits." in too_big
    assert "Push again" not in too_big
