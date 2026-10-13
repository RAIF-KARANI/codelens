"""Turning a review into a GitHub review: placement, repeats, the summary,
and defused @mentions. Pure functions; no database or GitHub."""

from core.diff import parse_unified_diff
from core.enums import FindingStatus, Severity
from core.github.publish import Publication, build_review, defuse
from core.providers import Usage
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
    # Not a mention: e-mail addresses and code stay as they are.
    assert defuse("mail a@b.com, `@decorator`") == "mail a@b.com, `@decorator`"
    assert defuse("@org/team and (@user)") == "@​org/team and (@​user)"
