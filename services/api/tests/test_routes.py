"""The /api/v1 routes against a real database. Jobs are either recorded, or
run in-process by the worker's real tasks (eager Celery), so the whole flow
(API -> queue -> worker -> API) runs without a broker or a GPU."""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_queue
from api.main import create_app
from api.queue import QueueUnavailable
from core.config import Settings

pytestmark = pytest.mark.integration

Sql = Callable[..., list[Any]]


class RecordingQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, ...]] = []

    async def index_repo(self, repo_id: int) -> None:
        self.jobs.append(("index_repo", repo_id))

    async def review(self, review_id: int, base: str) -> None:
        self.jobs.append(("review", review_id, base))


class InlineQueue(RecordingQueue):
    """Runs each job at once with the worker's tasks, as a worker would."""

    async def index_repo(self, repo_id: int) -> None:
        from worker.tasks import embed_repo, index_repo

        await super().index_repo(repo_id)
        await asyncio.to_thread(lambda: index_repo.apply(args=(repo_id,)).get())
        await asyncio.to_thread(lambda: embed_repo.apply(args=(repo_id,)).get())

    async def review(self, review_id: int, base: str) -> None:
        from worker.tasks import review

        await super().review(review_id, base)
        await asyncio.to_thread(lambda: review.apply(args=(review_id, base)).get())


class DownQueue(RecordingQueue):
    async def review(self, review_id: int, base: str) -> None:
        raise QueueUnavailable("Error 10061 connecting to redis:6379")


@contextmanager
def api(settings: Settings, queue: RecordingQueue) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_queue] = lambda: queue
    with TestClient(app) as client:
        yield client


def test_repos_list_detail_and_index_runs(
    settings: Settings, shop: Any, auth_headers: dict[str, str]
) -> None:
    with api(settings, RecordingQueue()) as c:
        repos = c.get("/api/v1/repos", headers=auth_headers).json()
        detail = c.get(f"/api/v1/repos/{shop.repo_id}", headers=auth_headers).json()
        runs = c.get(f"/api/v1/repos/{shop.repo_id}/index-runs", headers=auth_headers).json()
        missing = c.get("/api/v1/repos/999999", headers=auth_headers)

    [repo] = repos
    assert (repo["full_name"], repo["source"], repo["status"]) == ("local/shop", "local", "ready")
    assert repo["last_indexed_sha"] == shop.base_sha
    assert repo["last_review_at"] is None
    run = detail["latest_index_run"]
    assert run["status"] == "succeeded"
    assert run["stats"]["files"]["new"] > 0
    assert runs == {"items": [run], "next_cursor": None}
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_index_queues_a_job(
    settings: Settings, shop: Any, sql: Sql, auth_headers: dict[str, str]
) -> None:
    [github] = sql(
        "INSERT INTO repos (full_name, source) VALUES ('acme/app', 'github') RETURNING id"
    )
    queue = RecordingQueue()
    with api(settings, queue) as c:
        queued = c.post(f"/api/v1/repos/{shop.repo_id}/index", headers=auth_headers)
        refused = c.post(f"/api/v1/repos/{github.id}/index", headers=auth_headers)

    assert queued.status_code == 202
    assert queued.json() == {"repo_id": shop.repo_id, "status": "queued"}
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "unsupported_source"
    assert queue.jobs == [("index_repo", shop.repo_id)]


def test_review_requests_are_validated_before_queueing(
    settings: Settings, shop: Any, sql: Sql, auth_headers: dict[str, str]
) -> None:
    queue = RecordingQueue()
    with api(settings, queue) as c:

        def post(**body: Any) -> Any:
            return c.post(
                "/api/v1/reviews",
                headers=auth_headers,
                json={"repo_id": shop.repo_id, "base": "main", "head": shop.branch, **body},
            )

        unknown_repo = post(repo_id=999999)
        bad_head = post(head="no-such-branch")
        option_like = post(base="--output=/tmp/x")  # git would read it as an option
        empty = post(head="")

    assert unknown_repo.status_code == 404
    for response in (bad_head, option_like):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_revision"
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "invalid_request"
    assert queue.jobs == []
    assert sql("SELECT id FROM reviews") == []


def test_review_end_to_end(
    settings: Settings,
    shop: Any,
    worker_on_fakes: Any,
    auth_headers: dict[str, str],
) -> None:
    queue = InlineQueue()
    with api(settings, queue) as c:
        queued = c.post(
            "/api/v1/reviews",
            headers=auth_headers,
            json={"repo_id": shop.repo_id, "base": "main", "head": shop.branch},
        )
        review_id = queued.json()["review_id"]
        posted = c.get(f"/api/v1/reviews/{review_id}", headers=auth_headers).json()
        everything = c.get(
            f"/api/v1/reviews/{review_id}?include_dropped=true", headers=auth_headers
        ).json()
        listed = c.get(f"/api/v1/repos/{shop.repo_id}/reviews", headers=auth_headers).json()
        repo = c.get(f"/api/v1/repos/{shop.repo_id}", headers=auth_headers).json()

    assert queued.status_code == 202
    assert queued.json() == {"review_id": review_id, "status": "queued", "head_sha": shop.head_sha}
    # The base is pinned to the commit `main` named when the review was asked for.
    assert queue.jobs == [("review", review_id, shop.base_sha)]

    assert posted["status"] == "completed"
    assert (posted["source"], posted["model"]) == ("cli", "fake-llm")
    assert (posted["base_sha"], posted["head_sha"]) == (shop.base_sha, shop.head_sha)
    assert posted["counts"] == {"critical": 1, "warning": 0, "suggestion": 0}
    assert posted["pipeline_config"]["validation"]["anchor_window"] == 3  # default.yaml (A1)
    assert posted["token_usage"]["input_tokens"] > 0
    [finding] = posted["findings"]
    assert (finding["file_path"], finding["line_start"], finding["line_end"]) == (
        "shop/pricing.py",
        8,
        9,
    )

    assert [(f["file_path"], f["status"]) for f in everything["findings"]] == [
        ("shop/pricing.py", "posted"),
        ("shop/orders.py", "dropped"),
    ]
    assert everything["findings"][1]["drop_reason"]
    assert everything["counts"] == posted["counts"]  # counts are of posted findings only

    assert [r["id"] for r in listed["items"]] == [review_id]
    assert listed["items"][0]["counts"] == posted["counts"]
    assert repo["last_review_at"] is not None


def test_index_end_to_end(
    settings: Settings, shop: Any, worker_on_fakes: Any, auth_headers: dict[str, str]
) -> None:
    (shop.path / "shop" / "notes.py").write_text('"""Notes."""\n\n\ndef note():\n    return 1\n')
    shop.git("add", "-A")
    shop.git("commit", "-q", "-m", "Add notes")
    queue = InlineQueue()
    with api(settings, queue) as c:
        c.post(f"/api/v1/repos/{shop.repo_id}/index", headers=auth_headers)
        runs = c.get(f"/api/v1/repos/{shop.repo_id}/index-runs", headers=auth_headers).json()

    newest, first = runs["items"]
    assert newest["id"] > first["id"]
    assert newest["stats"]["files"]["new"] == 1
    assert newest["commit_sha"] == shop.git("rev-parse", "HEAD")


def test_reviews_page_and_filter(
    settings: Settings, shop: Any, sql: Sql, auth_headers: dict[str, str]
) -> None:
    ids = [
        sql(
            "INSERT INTO reviews (repo_id, source, head_sha, status)"
            " VALUES (:repo, 'cli', 'abc', :status) RETURNING id",
            repo=shop.repo_id,
            status=status,
        )[0].id
        for status in ("completed", "failed", "completed")
    ]
    url = f"/api/v1/repos/{shop.repo_id}/reviews"
    with api(settings, RecordingQueue()) as c:
        first = c.get(f"{url}?limit=2", headers=auth_headers).json()
        second = c.get(f"{url}?limit=2&cursor={first['next_cursor']}", headers=auth_headers).json()
        failed = c.get(f"{url}?status=failed", headers=auth_headers).json()
        bad_status = c.get(f"{url}?status=nope", headers=auth_headers)

    newest_first = ids[::-1]
    assert [r["id"] for r in first["items"]] == newest_first[:2]
    assert first["next_cursor"] == newest_first[1]
    assert [r["id"] for r in second["items"]] == newest_first[2:]
    assert second["next_cursor"] is None
    assert [r["id"] for r in failed["items"]] == [ids[1]]
    assert bad_status.status_code == 422


def test_a_review_that_cannot_be_queued_is_marked_failed(
    settings: Settings, shop: Any, sql: Sql, auth_headers: dict[str, str]
) -> None:
    with api(settings, DownQueue()) as c:
        response = c.post(
            "/api/v1/reviews",
            headers=auth_headers,
            json={"repo_id": shop.repo_id, "base": "main", "head": shop.branch},
        )

    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "queue_unavailable"
    assert "redis" not in body["message"]  # broker details stay in the server log
    [row] = sql("SELECT status, error FROM reviews")
    assert row.status == "failed"
    assert "could not enqueue" in row.error
