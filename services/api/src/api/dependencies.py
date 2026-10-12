"""FastAPI dependencies. Routes ask for clients through these rather than
reaching into app.state, so tests can swap them via `app.dependency_overrides`."""

from collections.abc import AsyncIterator

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from api.queue import JobQueue
from core.config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_engine(request: Request) -> AsyncEngine:
    return request.app.state.engine


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_queue(request: Request) -> JobQueue:
    return request.app.state.queue


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    # One session per request; creating it doesn't connect until first use.
    async with request.app.state.session_factory() as session:
        yield session
