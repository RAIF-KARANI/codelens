"""Reviewing a pull request and posting it on GitHub (step 3b): the PR head
from refs/pull/N/head, one COMMENT review, no repeats across pushes, and stale
heads superseded. Fake GitHub and fake models (tests/conftest.py)."""

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from core.providers.base import ProviderUnavailableError
from worker.tasks import index_repo, review

pytestmark = pytest.mark.integration

Sql = Callable[..., list[Any]]
TITLE = "Reject discounts above 100%"
GUARD_FROM = '    """Return amount after a percentage discount."""\n'
GUARD_TO = GUARD_FROM + "    if percent > 100:\n        return None\n"


@pytest.fixture
def pull_request(remote: Any, github: Any, attached_repo: int) -> dict[str, Any]:
    """octo/shop indexed at main, and PR #1 adding the guard (pushed as
    refs/pull/1/head, where GitHub keeps every PR's head, forks included)."""
    index_repo.apply(args=(attached_repo,)).get()
    base_sha = remote.git("rev-parse", "main")
    remote.git("checkout", "-q", "-b", "guard")
    pricing = (remote.work / "shop" / "pricing.py").read_text()
    head_sha = remote.commit("shop/pricing.py", pricing.replace(GUARD_FROM, GUARD_TO), TITLE)
    remote.push("HEAD:refs/pull/1/head")
    return {"repo_id": attached_repo, "base_sha": base_sha, "head_sha": head_sha}


def queue_review(sql: Sql, repo_id: int, head_sha: str) -> int:
    return sql(
        "INSERT INTO reviews (repo_id, source, pr_number, head_sha, status)"
        " VALUES (:repo, 'github', 1, :head, 'queued') RETURNING id",
        repo=repo_id,
        head=head_sha,
    )[0].id


def run(review_id: int, pr: dict[str, Any]) -> Any:
    return review.apply(args=(review_id, pr["base_sha"]), kwargs={"title": TITLE})


def test_a_pull_request_review_is_posted(
    pull_request: dict[str, Any], github: Any, worker_on_fakes: Any, sql: Sql
) -> None:
    review_id = queue_review(sql, pull_request["repo_id"], pull_request["head_sha"])

    summary = run(review_id, pull_request).get()

    assert summary["published"] is True
    [posted] = github.reviews
    assert posted["number"] == 1
    assert posted["event"] == "COMMENT"  # never REQUEST_CHANGES
    assert posted["commit_id"] == pull_request["head_sha"]
    [comment] = posted["comments"]
    assert {k: v for k, v in comment.items() if k != "body"} == {
        "path": "shop/pricing.py",
        "start_line": 8,
        "start_side": "RIGHT",
        "line": 9,
        "side": "RIGHT",
    }
    assert "critical" in comment["body"]
    assert "apply_discount can now return None" in comment["body"]
    assert "**1 new finding**" in posted["body"]
    # The PR's title reached the prompt.
    assert TITLE in worker_on_fakes.calls[0].messages[-1].content

    [row] = sql("SELECT status, github_review_id FROM reviews WHERE id = :id", id=review_id)
    assert (row.status, row.github_review_id) == ("completed", 9001)
    end, published = sql("SELECT type, payload FROM review_events ORDER BY seq DESC LIMIT 2")
    assert published.type == "published"
    assert published.payload == {"github_review_id": 9001, "new": 1, "repeated": 0, "inline": 1}
    # Posting came after the pipeline, so the worker, not the pipeline, ends it.
    assert (end.type, end.payload) == ("end", {"status": "completed"})


def test_a_new_push_does_not_repeat_findings(
    pull_request: dict[str, Any], remote: Any, github: Any, sql: Sql
) -> None:
    run(queue_review(sql, pull_request["repo_id"], pull_request["head_sha"]), pull_request).get()

    # Another commit on the PR; the guard (and so the same finding) is still there.
    head = remote.commit("shop/notes.py", '"""Notes."""\n', "Add notes")
    remote.push("HEAD:refs/pull/1/head")
    second = run(queue_review(sql, pull_request["repo_id"], head), pull_request).get()

    assert second["published"] is True
    first_post, second_post = github.reviews
    assert len(first_post["comments"]) == 1
    assert second_post["comments"] == []  # not posted again
    assert second_post["commit_id"] == head
    # The user's choice (D101): every reviewed push still gets a summary.
    assert "**No new findings** in this push." in second_post["body"]
    assert "1 more on code already commented on in an earlier push" in second_post["body"]

    # A third push changes apply_discount itself: a comment on it is new again.
    pricing = (remote.work / "shop" / "pricing.py").read_text()
    head = remote.commit(
        "shop/pricing.py", pricing.replace("return None", "return 0.0"), "Return 0 instead"
    )
    remote.push("HEAD:refs/pull/1/head")
    run(queue_review(sql, pull_request["repo_id"], head), pull_request).get()

    third_post = github.reviews[2]
    assert len(third_post["comments"]) == 1
    assert "**1 new finding**" in third_post["body"]


def test_a_review_of_an_older_push_is_superseded(
    pull_request: dict[str, Any], github: Any, worker_on_fakes: Any, sql: Sql
) -> None:
    older = queue_review(sql, pull_request["repo_id"], pull_request["head_sha"])
    queue_review(sql, pull_request["repo_id"], "f" * 40)  # a newer push, queued after it

    summary = run(older, pull_request).get()

    assert summary == {"review_id": older, "status": "superseded", "skipped": True}
    assert worker_on_fakes.calls == []  # never reviewed
    assert github.reviews == []
    [row] = sql("SELECT status, error FROM reviews WHERE id = :id", id=older)
    assert row.status == "superseded"
    assert "before it ran" in row.error
    events = sql("SELECT type FROM review_events WHERE review_id = :id ORDER BY seq", id=older)
    assert [e.type for e in events] == ["superseded", "end"]


def test_a_push_during_the_review_stops_it_being_posted(
    pull_request: dict[str, Any], github: Any, worker_on_fakes: Any, sql: Sql
) -> None:
    review_id = queue_review(sql, pull_request["repo_id"], pull_request["head_sha"])
    scripted = worker_on_fakes.responder

    def push_while_generating(messages: Any, schema: Any) -> Any:
        # The webhook for a newer push lands while the model is busy. (A
        # thread: sql() runs its own event loop, and this one is in use.)
        thread = threading.Thread(
            target=queue_review, args=(sql, pull_request["repo_id"], "e" * 40)
        )
        thread.start()
        thread.join()
        return scripted(messages, schema)

    worker_on_fakes.responder = push_while_generating

    summary = run(review_id, pull_request).get()

    assert summary["status"] == "superseded"
    assert summary["published"] is False
    assert github.reviews == []
    [row] = sql("SELECT status, error FROM reviews WHERE id = :id", id=review_id)
    assert row.status == "superseded"
    assert "not posted" in row.error
    # The findings are still recorded, for the trace and the dashboard.
    assert sql("SELECT count(*) FROM findings WHERE review_id = :id", id=review_id)[0][0] == 2


def test_refused_inline_comments_fall_back_to_the_body(
    pull_request: dict[str, Any], github: Any, sql: Sql
) -> None:
    github.refuse_inline = 1

    run(queue_review(sql, pull_request["repo_id"], pull_request["head_sha"]), pull_request).get()

    [posted] = github.reviews
    assert posted["comments"] == []
    assert "Findings that don't sit on a line of this diff" in posted["body"]
    assert "`shop/pricing.py:8` apply_discount can now return None" in posted["body"]


def test_a_missing_pull_request_fails_the_review(
    pull_request: dict[str, Any], github: Any, sql: Sql
) -> None:
    review_id = sql(
        "INSERT INTO reviews (repo_id, source, pr_number, head_sha, status)"
        " VALUES (:repo, 'github', 2, :head, 'queued') RETURNING id",
        repo=pull_request["repo_id"],
        head=pull_request["head_sha"],
    )[0].id

    result = review.apply(args=(review_id, pull_request["base_sha"]))

    assert "#2" in str(result.result)
    [row] = sql("SELECT status, github_review_id FROM reviews WHERE id = :id", id=review_id)
    assert row.status == "failed"
    # The pull request is told (Phase 11 step 4), with a reason from the
    # fixed list, not the git error itself.
    [note] = github.reviews
    assert note["number"] == 2
    assert "couldn't review" in note["body"]
    assert "The pull request's code couldn't be fetched from GitHub." in note["body"]
    assert "fatal" not in note["body"] and "refs/pull" not in note["body"]
    assert note.get("comments") is None
    assert row.github_review_id == 9001


def test_a_review_of_a_head_the_pr_has_moved_past_is_superseded(
    pull_request: dict[str, Any], remote: Any, github: Any, worker_on_fakes: Any, sql: Sql
) -> None:
    # Phase 9 step 8 (D134): a replayed (or late) "opened" delivery queued a
    # review of an old head with the highest id, so nothing newer was queued
    # to mark it stale, and the worker reviewed and posted the old commit.
    head = remote.commit("shop/notes.py", '"""Notes."""\n', "Add notes")
    remote.push("HEAD:refs/pull/1/head")
    run(queue_review(sql, pull_request["repo_id"], head), pull_request).get()  # reviewed
    stale = queue_review(sql, pull_request["repo_id"], pull_request["head_sha"])
    calls = len(worker_on_fakes.calls)

    summary = run(stale, pull_request).get()

    assert summary == {"review_id": stale, "status": "superseded", "skipped": True}
    assert len(worker_on_fakes.calls) == calls  # the model never saw it
    assert len(github.reviews) == 1  # only the current head's review was posted
    [row] = sql("SELECT status, error FROM reviews WHERE id = :id", id=stale)
    assert row.status == "superseded"
    assert f"the pull request's head is now {head[:7]}" in row.error


def test_a_review_is_posted_to_the_repo_by_its_current_name(
    pull_request: dict[str, Any], github: Any, sql: Sql, tmp_path: Path
) -> None:
    # Renamed on GitHub after it was indexed; no event told us (D135).
    remotes = tmp_path / "remotes" / "octo"
    (remotes / "shop.git").rename(remotes / "shop-renamed.git")
    github.names[42] = "octo/shop-renamed"
    run(queue_review(sql, pull_request["repo_id"], pull_request["head_sha"]), pull_request).get()

    [posted] = github.reviews
    assert posted["repo"] == "octo/shop-renamed"
    [repo] = sql("SELECT full_name FROM repos WHERE id = :id", id=pull_request["repo_id"])
    assert repo.full_name == "octo/shop-renamed"


# --- a failed review says so on the pull request (Phase 11 step 4)


def fail_every_chunk(worker_on_fakes: Any) -> None:
    def responder(messages: Any, schema: Any) -> Any:
        raise ProviderUnavailableError("http://host.docker.internal:11434: connection refused")

    worker_on_fakes.responder = responder


def test_a_review_whose_every_chunk_failed_says_so(
    pull_request: dict[str, Any], github: Any, worker_on_fakes: Any, sql: Sql
) -> None:
    fail_every_chunk(worker_on_fakes)
    review_id = queue_review(sql, pull_request["repo_id"], pull_request["head_sha"])

    summary = run(review_id, pull_request).get()

    assert summary["status"] == "failed"
    [note] = github.reviews
    assert note["body"].startswith(f"### CodeLens couldn't review `{pull_request['head_sha'][:7]}`")
    assert "The model couldn't review any part of it." in note["body"]
    assert "11434" not in note["body"]  # the error stays in the dashboard
    [row] = sql("SELECT status, github_review_id FROM reviews WHERE id = :id", id=review_id)
    assert (row.status, row.github_review_id) == ("failed", 9001)
    types = [e.type for e in sql(
        "SELECT type FROM review_events WHERE review_id = :id ORDER BY seq", id=review_id
    )]  # fmt: skip
    assert types[-2:] == ["failure_posted", "end"]


def test_no_note_when_a_newer_push_is_queued(
    pull_request: dict[str, Any], github: Any, worker_on_fakes: Any, sql: Sql
) -> None:
    fail_every_chunk(worker_on_fakes)
    review_id = queue_review(sql, pull_request["repo_id"], pull_request["head_sha"])
    scripted = worker_on_fakes.responder

    def push_while_generating(messages: Any, schema: Any) -> Any:
        thread = threading.Thread(
            target=queue_review, args=(sql, pull_request["repo_id"], "e" * 40)
        )
        thread.start()
        thread.join()
        return scripted(messages, schema)

    worker_on_fakes.responder = push_while_generating

    run(review_id, pull_request).get()

    assert github.reviews == []  # the newer push will say what it found


def test_no_note_for_an_account_no_longer_allowed(
    pull_request: dict[str, Any], github: Any, worker_on_fakes: Any, sql: Sql
) -> None:
    review_id = queue_review(sql, pull_request["repo_id"], pull_request["head_sha"])
    sql("UPDATE installations SET suspended_at = now()")

    result = review.apply(args=(review_id, pull_request["base_sha"]))

    assert "no longer allowed" in str(result.result)
    assert github.reviews == []  # CodeLens doesn't act for it at all (D130)


def test_a_note_github_refuses_is_only_logged(
    pull_request: dict[str, Any], github: Any, worker_on_fakes: Any, sql: Sql
) -> None:
    fail_every_chunk(worker_on_fakes)
    github.down = True
    review_id = queue_review(sql, pull_request["repo_id"], pull_request["head_sha"])

    summary = run(review_id, pull_request).get()

    assert summary["status"] == "failed"
    [row] = sql("SELECT status, github_review_id FROM reviews WHERE id = :id", id=review_id)
    assert (row.status, row.github_review_id) == ("failed", None)
