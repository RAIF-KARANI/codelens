"""FastAPI dependencies. Routes ask for clients through these rather than
reaching into app.state, so tests can swap them via `app.dependency_overrides`."""

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine


def get_engine(request: Request) -> AsyncEngine:
    return request.app.state.engine


def get_redis(request: Request) -> Redis:
    return request.app.state.redis
