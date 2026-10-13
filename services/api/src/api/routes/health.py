"""Readiness endpoint: 200 when Postgres and Redis both answer, 503 otherwise.
A demo instance (DEMO_MODE) has no Redis, so only Postgres counts there."""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from api.checks import CheckResult, check_database, check_redis
from api.dependencies import get_engine, get_redis

router = APIRouter(tags=["health"])


class HealthChecks(BaseModel):
    database: CheckResult
    redis: CheckResult | None  # None: not used (a demo instance)


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: HealthChecks


@router.get(
    "/health",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def health(
    response: Response,
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    redis: Annotated[Redis | None, Depends(get_redis)],
) -> HealthResponse:
    if redis is None:
        database, cache = await check_database(engine), None
    else:
        # Both probes at once, so the worst case is one timeout, not two.
        database, cache = await asyncio.gather(check_database(engine), check_redis(redis))
    healthy = database.ok and (cache is None or cache.ok)
    if not healthy:
        # A non-2xx code is what load balancers / compose healthchecks act on.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if healthy else "unavailable",
        checks=HealthChecks(database=database, redis=cache),
    )
