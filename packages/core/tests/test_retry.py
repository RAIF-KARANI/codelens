"""Reviewing a pull request's push again (Phase 11 step 8, D181): the Review
again button's rules, and which failures the poller queues again once Ollama
answers."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from core.db import create_engine
from core.github.retry import MAX_OUTAGE_TRIES, RetryRefused, model_outages, retry_review

pytestmark = pytest.mark.integration

OUTAGE = "ProviderUnavailableError('cannot reach Ollama at http://host.docker.internal:11434')"


def in_db(database_url: str, body: Callable[[AsyncConnection], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        engine = create_engine(database_url, pooled=False)
        try:
            async with engine.begin() as conn:
                repo = "INSERT INTO repos (id, full_name, source) VALUES (1, 'octo/shop', 'github')"
                await conn.execute(text(repo))
                return await body(conn)
        finally:
            await engine.dispose()

    return asyncio.run(go())


async def add(
    conn: AsyncConnection,
    *,
    pr: int | None = 1,
    head: str = "a" * 40,
    status: str = "failed",
    error: str | None = OUTAGE,
    source: str = "github",
    age: timedelta = timedelta(minutes=1),
) -> int:
    return await conn.scalar(
        text(
            "INSERT INTO reviews (repo_id, source, pr_number, head_sha, status, error, title,"
            " finished_at) VALUES (1, :source, :pr, :head, :status, :error, 'Fix it',"
            " now() - CAST(:age AS interval)) RETURNING id"
        ),
        {"source": source, "pr": pr, "head": head, "status": status, "error": error, "age": age},
    )


def test_a_failed_review_is_queued_again_for_the_same_commit(migrated_database_url: str) -> None:
    async def body(conn: AsyncConnection) -> Any:
        failed = await add(conn)
        queued = await retry_review(conn, failed)
        row = (
            await conn.execute(
                text("SELECT pr_number, head_sha, status, title FROM reviews WHERE id = :id"),
                {"id": queued.review_id},
            )
        ).one()
        return failed, queued, row

    failed, queued, row = in_db(migrated_database_url, body)
    assert queued.review_id > failed
    # The worker looks the PR's base up on GitHub: a review that failed early has none.
    assert (queued.base_sha, queued.title) == ("", "Fix it")
    assert tuple(row) == (1, "a" * 40, "queued", "Fix it")


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        (lambda conn: add(conn, status="completed", error=None), "not_retryable"),
        (lambda conn: add(conn, source="cli", pr=None), "not_retryable"),
        (lambda conn: asyncio.sleep(0, result=999), "not_found"),
    ],
)
def test_only_a_failed_pull_request_review_can_be_tried_again(
    migrated_database_url: str, setup: Callable[[AsyncConnection], Awaitable[int]], code: str
) -> None:
    async def body(conn: AsyncConnection) -> str:
        with pytest.raises(RetryRefused) as refused:
            await retry_review(conn, await setup(conn))
        return refused.value.code

    assert in_db(migrated_database_url, body) == code


def test_only_the_pull_requests_newest_review(migrated_database_url: str) -> None:
    async def body(conn: AsyncConnection) -> str:
        failed = await add(conn)
        await add(conn, head="b" * 40, status="queued", error=None)  # a later push
        with pytest.raises(RetryRefused) as refused:
            await retry_review(conn, failed)
        return str(refused.value)

    assert "is newer" in in_db(migrated_database_url, body)


def test_ollama_coming_back_retries_only_its_own_recent_failures(
    migrated_database_url: str,
) -> None:
    async def body(conn: AsyncConnection) -> tuple[list[int], list[int]]:
        outage = await add(conn, pr=1)
        other = await add(conn, pr=2, error="GitHubError(404)")  # not an outage
        await add(conn, pr=3, age=timedelta(days=2))  # too long ago
        superseded = await add(conn, pr=4)
        await add(conn, pr=4, head="b" * 40, status="completed", error=None)  # reviewed since
        return [outage], [other, superseded, *await model_outages(conn)]

    expected, found = in_db(migrated_database_url, body)
    assert found[2:] == expected


def test_a_commit_that_keeps_failing_stops_being_retried(migrated_database_url: str) -> None:
    async def body(conn: AsyncConnection) -> tuple[list[int], list[int]]:
        for _ in range(MAX_OUTAGE_TRIES - 1):
            await add(conn)
        before = await model_outages(conn)
        await add(conn)  # one more failure of the same commit
        return before, await model_outages(conn)

    before, after = in_db(migrated_database_url, body)
    assert len(before) == 1
    assert after == []
