"""Readiness endpoint.

| status | HTTP | when |
|---|---|---|
| ok | 200 | every check passes |
| degraded | 200 | Postgres and Redis answer, Ollama doesn't (or lacks a model) |
| unavailable | 503 | Postgres or Redis is down |

Degraded is still a 200: the API serves pages and accepts webhooks without
Ollama (reviews wait or fail), and restarting it wouldn't help.

Only what this instance uses is checked: a demo (DEMO_MODE) has no Redis and
no models, and the fake providers (tests, CI) need no Ollama (D122).
"""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from api.checks import CheckResult, check_database, check_ollama, check_redis
from api.dependencies import get_engine, get_redis, get_settings
from core.config import Settings

router = APIRouter(tags=["health"])


class HealthChecks(BaseModel):
    database: CheckResult
    redis: CheckResult | None  # None: not used (a demo instance)
    ollama: CheckResult | None = None  # None: not used (a demo, or fake providers)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    checks: HealthChecks


async def _skipped() -> None:
    return None


def _ollama_models(settings: Settings) -> list[str]:
    """The Ollama models this instance's workers need, if any."""
    if settings.demo_mode:
        return []
    models = []
    if settings.llm_provider == "ollama":
        models.append(settings.llm_model)
    if settings.embed_provider == "ollama":
        models.append(settings.embed_model)
    return models


@router.get(
    "/health",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def health(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    redis: Annotated[Redis | None, Depends(get_redis)],
) -> HealthResponse:
    models = _ollama_models(settings)
    # All probes at once, so the worst case is one timeout, not three.
    database, cache, ollama = await asyncio.gather(
        check_database(engine),
        check_redis(redis) if redis is not None else _skipped(),
        check_ollama(settings.ollama_base_url, models) if models else _skipped(),
    )
    if not database.ok or (cache is not None and not cache.ok):
        # A non-2xx code is what load balancers / compose healthchecks act on.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        overall = "unavailable"
    elif ollama is not None and not ollama.ok:
        # Still 200: the API itself works, and restarting it wouldn't help.
        overall = "degraded"
    else:
        overall = "ok"
    return HealthResponse(
        status=overall, checks=HealthChecks(database=database, redis=cache, ollama=ollama)
    )
