"""The worker's tasks, run in-process with `.apply()` (eager: no broker). The
integration tests use the test database and fake models (services/conftest.py)."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import text

import worker.tasks
from core import jobs
from core.config import Settings
from core.db import create_engine
from core.indexing import IndexingError, IndexLocked
from core.review.pipeline import ReviewError
from worker.celery_app import celery_app
from worker.tasks import LOCK_MAX_RETRIES, index_repo, ping, review

Sql = Callable[..., list[Any]]


def test_ping_runs_eagerly() -> None:
    # .apply() executes in-process without a broker.
    assert ping.apply().get() == "pong"


@pytest.mark.parametrize(
    ("task", "queue"),
    [
        (jobs.PING, jobs.INDEX_QUEUE),
        (jobs.INDEX_REPO, jobs.INDEX_QUEUE),
        (jobs.EMBED_REPO, jobs.GPU_QUEUE),
        (jobs.REVIEW, jobs.GPU_QUEUE),
    ],
)
def test_every_job_is_registered_and_routed(task: str, queue: str) -> None:
    assert task in celery_app.tasks
    assert celery_app.amqp.router.route({}, task)["queue"].name == queue


def queued_review(sql: Sql, repo_id: int, head_sha: str, status: str = "queued") -> int:
    return sql(
        "INSERT INTO reviews (repo_id, source, head_sha, status)"
        " VALUES (:repo, 'cli', :head, :status) RETURNING id",
        repo=repo_id,
        head=head_sha,
        status=status,
    )[0].id


@pytest.mark.integration
def test_review_claims_the_queued_row(shop: Any, sql: Sql, worker_on_fakes: Any) -> None:
    review_id = queued_review(sql, shop.repo_id, shop.head_sha)

    summary = review.apply(args=(review_id, shop.base_sha)).get()

    assert summary == {"review_id": review_id, "status": "completed", "posted": 1}
    [row] = sql("SELECT status, base_sha, model, started_at FROM reviews")  # no second row
    assert (row.status, row.base_sha, row.model) == ("completed", shop.base_sha, "fake-llm")
    assert row.started_at is not None


@pytest.mark.integration
def test_review_restarts_a_crashed_attempt(shop: Any, sql: Sql, worker_on_fakes: Any) -> None:
    # A worker died mid-review: the row is `running`, with part of a timeline
    # and a finding. The redelivered task must start over, not append.
    review_id = queued_review(sql, shop.repo_id, shop.head_sha, status="running")
    sql(
        "INSERT INTO review_events (review_id, seq, type)"
        " VALUES (:id, 1, 'stage'), (:id, 2, 'stage')",
        id=review_id,
    )
    sql(
        "INSERT INTO findings (review_id, file_path, line_start, line_end, severity, category,"
        " title, explanation) VALUES (:id, 'stale.py', 1, 1, 'warning', 'bug', 'stale', '')",
        id=review_id,
    )

    review.apply(args=(review_id, shop.base_sha)).get()

    seqs = [r.seq for r in sql("SELECT seq FROM review_events ORDER BY seq")]
    assert seqs == list(range(1, len(seqs) + 1))
    last_two = sql("SELECT type FROM review_events ORDER BY seq DESC LIMIT 2")
    assert [r.type for r in last_two] == ["end", "done"]
    assert [r.file_path for r in sql("SELECT file_path FROM findings")] == [
        "shop/pricing.py",
        "shop/orders.py",
    ]


@pytest.mark.integration
def test_review_skips_a_finished_review(shop: Any, sql: Sql, worker_on_fakes: Any) -> None:
    # Acks are late, so a finished review's message can be delivered again.
    review_id = queued_review(sql, shop.repo_id, shop.head_sha, status="completed")

    summary = review.apply(args=(review_id, shop.base_sha)).get()

    assert summary["skipped"] is True
    assert worker_on_fakes.calls == []  # the model was never asked


@pytest.mark.integration
def test_review_failing_before_the_pipeline_marks_the_row(
    shop: Any, sql: Sql, worker_on_fakes: Any
) -> None:
    review_id = queued_review(sql, shop.repo_id, shop.head_sha)

    result = review.apply(args=(review_id, "no-such-branch"))

    assert isinstance(result.result, ReviewError)
    [row] = sql("SELECT status, error, finished_at FROM reviews")
    assert row.status == "failed"
    assert "no-such-branch" in row.error
    assert row.finished_at is not None
    # A streamer is told why, and that the stream is over.
    events = sql("SELECT type, payload FROM review_events ORDER BY seq")
    assert [e.type for e in events] == ["error", "end"]
    assert "no-such-branch" in events[0].payload["message"]
    assert events[1].payload == {"status": "failed"}


@pytest.mark.integration
def test_every_event_rings_the_doorbell(
    shop: Any, sql: Sql, worker_on_fakes: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rings: list[int] = []

    @asynccontextmanager
    async def recording_doorbell(settings: Any) -> AsyncIterator[Any]:
        async def notify(review_id: int) -> None:
            rings.append(review_id)

        yield notify

    monkeypatch.setattr(worker.tasks, "doorbell", recording_doorbell)
    review_id = queued_review(sql, shop.repo_id, shop.head_sha)

    review.apply(args=(review_id, shop.base_sha)).get()

    events = sql("SELECT type FROM review_events ORDER BY seq")
    assert events[-1].type == "end"
    # One ring per event, each after its row was committed.
    assert rings == [review_id] * len(events)


@pytest.mark.integration
def test_index_refuses_a_repo_it_cannot_reach(sql: Sql, worker_on_fakes: Any) -> None:
    [github] = sql(
        "INSERT INTO repos (full_name, source) VALUES ('acme/app', 'github') RETURNING id"
    )
    [local] = sql(
        "INSERT INTO repos (full_name, source) VALUES ('local/gone', 'local') RETURNING id"
    )

    uninstalled = index_repo.apply(args=(github.id,))
    no_checkout = index_repo.apply(args=(local.id,))

    assert isinstance(uninstalled.result, IndexingError)
    assert "not covered by any installation" in str(uninstalled.result)
    assert isinstance(no_checkout.result, IndexingError)
    assert "no local checkout" in str(no_checkout.result)


@pytest.mark.integration
def test_index_retries_while_another_run_holds_the_lock(
    shop: Any, settings: Settings, monkeypatch: pytest.MonkeyPatch, worker_on_fakes: Any
) -> None:
    attempts = 0
    original = worker.tasks._index

    async def counting(*args: Any) -> Any:
        nonlocal attempts
        attempts += 1
        return await original(*args)

    monkeypatch.setattr(worker.tasks, "_index", counting)

    async def go() -> Any:
        engine = create_engine(settings.database_url, pooled=False)
        try:
            async with engine.connect() as conn:
                # Hold the same advisory lock a running index takes (D41).
                await conn.execute(text("SELECT pg_advisory_lock(hashtext('index:local/shop'))"))
                # One retry left: eager .apply() runs the retry at once, which
                # hits the lock again and gives up.
                return await asyncio.to_thread(
                    index_repo.apply, args=(shop.repo_id,), retries=LOCK_MAX_RETRIES - 1
                )
        finally:
            await engine.dispose()

    result = asyncio.run(go())

    assert attempts == 2
    assert isinstance(result.result, IndexLocked)
