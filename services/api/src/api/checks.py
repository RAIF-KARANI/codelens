"""Dependency probes used by /health."""

import asyncio
import logging
import time
from collections.abc import Awaitable

from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# A health probe must answer fast even when a dependency hangs (e.g. a
# firewalled port that never refuses), or the orchestrator's probe times out first.
CHECK_TIMEOUT_SECONDS = 2.0


class CheckResult(BaseModel):
    ok: bool
    latency_ms: float
    # Exception class name only. Full messages can leak hostnames/credentials
    # to whoever can reach /health; they go to the server log instead.
    error: str | None = None


async def _run_check(name: str, probe: Awaitable[object]) -> CheckResult:
    start = time.perf_counter()
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await probe
    except Exception as exc:  # any failure means "unhealthy", never a 500
        logger.warning("health check %s failed: %r", name, exc)
        return CheckResult(ok=False, latency_ms=_elapsed_ms(start), error=type(exc).__name__)
    return CheckResult(ok=True, latency_ms=_elapsed_ms(start))


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


async def _select_one(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def check_database(engine: AsyncEngine) -> CheckResult:
    return await _run_check("database", _select_one(engine))


async def check_redis(redis: Redis) -> CheckResult:
    return await _run_check("redis", redis.ping())
