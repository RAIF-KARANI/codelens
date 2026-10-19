"""Reviewing a pull request's push again (Phase 11 step 8, D181): the Review
again route, and the poller queuing the Ollama outages again once Ollama
answers. Against a real database; jobs recorded, not run."""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_queue
from api.main import create_app
from api.poller import requeue_model_outages
from api.queue import QueueUnavailable
from core.config import Settings
from core.db import create_engine

pytestmark = pytest.mark.integration

Sql = Callable[..., list[Any]]
OUTAGE = "ProviderUnavailableError('cannot reach Ollama at http://host.docker.internal:11434')"


class Queue:
    def __init__(self, down: bool = False) -> None:
        self.reviews: list[tuple[int, str, str | None]] = []
        self.down = down

    async def index_repo(self, repo_id: int) -> None:
        raise AssertionError("not expected")

    async def review(self, review_id: int, base: str, *, title: str | None = None) -> None:
        if self.down:
            raise QueueUnavailable("Error 10061 connecting to redis:6379")
        self.reviews.append((review_id, base, title))


@contextmanager
def api(settings: Settings, queue: Queue) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_queue] = lambda: queue
    with TestClient(app) as client:
        yield client


def failed_review(sql: Sql, *, status: str = "failed", error: str | None = OUTAGE) -> int:
    [repo] = sql(
        "INSERT INTO repos (full_name, source) VALUES ('octo/shop', 'github') RETURNING id"
    )
    [row] = sql(
        "INSERT INTO reviews (repo_id, source, pr_number, title, head_sha, status, error,"
        " finished_at) VALUES (:repo, 'github', 7, 'Fix it', :head, :status, :error, now())"
        " RETURNING id",
        repo=repo.id,
        head="a" * 40,
        status=status,
        error=error,
    )
    return row.id


def test_review_again_queues_the_same_commit(
    settings: Settings, sql: Sql, auth_headers: dict[str, str]
) -> None:
    failed = failed_review(sql)
    queue = Queue()
    with api(settings, queue) as c:
        answer = c.post(f"/api/v1/reviews/{failed}/retry", headers=auth_headers)

    assert answer.status_code == 202
    body = answer.json()
    assert body["status"] == "queued"
    assert body["head_sha"] == "a" * 40
    # No base: the worker asks GitHub for the PR's base now.
    assert queue.reviews == [(body["review_id"], "", "Fix it")]


def test_only_a_failed_review_and_never_on_the_demo(
    settings: Settings, sql: Sql, auth_headers: dict[str, str]
) -> None:
    completed = failed_review(sql, status="completed", error=None)
    with api(settings, Queue()) as c:
        refused = c.post(f"/api/v1/reviews/{completed}/retry", headers=auth_headers)
        missing = c.post("/api/v1/reviews/99999/retry", headers=auth_headers)
    with api(settings.model_copy(update={"demo_mode": True}), Queue()) as c:
        demo = c.post(f"/api/v1/reviews/{completed}/retry", headers=auth_headers)

    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "not_retryable")
    assert missing.status_code == 404
    assert (demo.status_code, demo.json()["error"]["code"]) == (403, "demo_mode")


def test_a_queue_that_is_down_leaves_the_retry_failed(
    settings: Settings, sql: Sql, auth_headers: dict[str, str]
) -> None:
    failed = failed_review(sql)
    with api(settings, Queue(down=True)) as c:
        answer = c.post(f"/api/v1/reviews/{failed}/retry", headers=auth_headers)

    assert answer.status_code == 503
    [retry] = sql("SELECT status, error FROM reviews WHERE id > :id", id=failed)
    assert retry.status == "failed"
    assert "could not enqueue" in retry.error


def sweep(settings: Settings, queue: Queue, ollama_up: bool) -> list[int]:
    async def go() -> list[int]:
        engine = create_engine(settings.database_url, pooled=False)
        asked: list[bool] = []

        async def answers() -> bool:
            asked.append(True)
            return ollama_up

        try:
            return await requeue_model_outages(engine, queue, answers)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_the_poller_retries_outages_once_ollama_answers(settings: Settings, sql: Sql) -> None:
    failed = failed_review(sql)
    queue = Queue()

    assert sweep(settings, queue, ollama_up=False) == []  # still down: nothing yet
    [again] = sweep(settings, queue, ollama_up=True)

    assert again > failed
    assert queue.reviews == [(again, "", "Fix it")]
    # The retry is now the PR's newest review: the next sweep leaves it alone.
    assert sweep(settings, queue, ollama_up=True) == []


def test_the_poller_leaves_other_failures_alone(settings: Settings, sql: Sql) -> None:
    failed_review(sql, error="GitHubError(404, 'Not Found')")
    queue = Queue()

    assert sweep(settings, queue, ollama_up=True) == []
    assert queue.reviews == []
