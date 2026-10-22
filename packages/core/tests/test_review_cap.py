"""GITHUB_REVIEWS_PER_HOUR holds when work for one account arrives at once,
and when a review is tried again (D186; security audit run-2)."""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.accounts import Accounts
from core.db import create_engine
from core.github.retry import RetryRefused, retry_review
from core.github.webhooks import Policy, apply_event

pytestmark = pytest.mark.integration

LIMIT = 1
POLICY = Policy(Accounts.parse("octo:42"), reviews_per_hour=LIMIT)


def opened(number: int, head: str) -> dict[str, Any]:
    return {
        "pull_request": {
            "number": number,
            "state": "open",
            "draft": False,
            "title": f"PR {number}",
            "head": {"sha": head},
            "base": {"sha": "b" * 40},
        },
        "repository": {"id": 9001, "full_name": "octo/shop"},
    }


async def seed(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO installations (id, github_installation_id, account_id, account_login,"
                " account_type) VALUES (1, 555, 42, 'octo', 'Organization')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO repos (id, github_id, full_name, source, installation_id)"
                " VALUES (1, 9001, 'octo/shop', 'github', 1)"
            )
        )


async def statuses(engine: AsyncEngine) -> list[tuple[int, str]]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT pr_number, status FROM reviews ORDER BY id"))
        return [(pr, status) for pr, status in rows]


def run(database_url: str, body: Any) -> Any:
    async def go() -> Any:
        engine = create_engine(database_url, pooled=False)
        try:
            await seed(engine)
            return await body(engine)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_two_pull_requests_at_once_share_one_hours_limit(migrated_database_url: str) -> None:
    async def body(engine: AsyncEngine) -> tuple[bool, list[tuple[int, str]]]:
        # The first intake counts and inserts, but hasn't committed yet: before
        # D186 the second one, counting at that moment, saw no review and
        # passed the limit too.
        async with engine.connect() as first:
            transaction = await first.begin()
            await apply_event(first, "pull_request", "opened", opened(1, "a" * 40), POLICY)

            async def second() -> None:
                async with engine.begin() as conn:
                    await apply_event(conn, "pull_request", "opened", opened(2, "c" * 40), POLICY)

            waiting = asyncio.create_task(second())
            await asyncio.sleep(0.5)
            blocked = not waiting.done()  # it waits for the account's lock
            await transaction.commit()
            await waiting
        return blocked, await statuses(engine)

    blocked, rows = run(migrated_database_url, body)
    assert blocked
    assert rows == [(1, "queued"), (2, "failed")]


def test_review_again_counts_against_the_limit(migrated_database_url: str) -> None:
    async def body(engine: AsyncEngine) -> tuple[str, list[tuple[int, str]]]:
        async with engine.begin() as conn:
            await apply_event(conn, "pull_request", "opened", opened(1, "a" * 40), POLICY)
            refused = await apply_event(conn, "pull_request", "opened", opened(2, "c" * 40), POLICY)
        assert refused.reviews == []  # the limit recorded PR 2 as failed
        async with engine.connect() as conn:
            failed = await conn.scalar(text("SELECT id FROM reviews WHERE pr_number = 2"))
        with pytest.raises(RetryRefused) as over:
            async with engine.begin() as conn:
                await retry_review(conn, failed, reviews_per_hour=LIMIT)
        return over.value.code, await statuses(engine)

    code, rows = run(migrated_database_url, body)
    assert code == "over_limit"
    assert rows == [(1, "queued"), (2, "failed")]  # nothing new was queued


def test_review_again_without_a_limit_still_queues(migrated_database_url: str) -> None:
    async def body(engine: AsyncEngine) -> list[tuple[int, str]]:
        async with engine.begin() as conn:
            await apply_event(conn, "pull_request", "opened", opened(1, "a" * 40), POLICY)
            await apply_event(conn, "pull_request", "opened", opened(2, "c" * 40), POLICY)
            failed = await conn.scalar(text("SELECT id FROM reviews WHERE pr_number = 2"))
            await retry_review(conn, failed, reviews_per_hour=0)
        return await statuses(engine)

    assert run(migrated_database_url, body) == [(1, "queued"), (2, "failed"), (2, "queued")]
