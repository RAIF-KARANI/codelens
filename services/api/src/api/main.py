"""FastAPI application entrypoint: `uvicorn api.main:app`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from api.errors import install_error_handlers
from api.queue import CeleryJobQueue
from api.routes import health, inspect, repos, reviews, webhooks
from core.config import Settings, get_settings
from core.db import create_engine, create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Shared clients are created once per process, not per request. None of
    # them connects eagerly, so the API still boots (and /health reports the
    # problem) when Postgres or Redis is down.
    settings: Settings = app.state.settings
    app.state.engine = create_engine(settings.database_url)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    app.state.events_redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
    app.state.queue = CeleryJobQueue(settings.redis_url)
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.events_redis.aclose()
        await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    # A factory lets tests build an app with their own settings, and gives one
    # obvious place to register routers.
    app = FastAPI(title="CodeLens API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(repos.router)
    app.include_router(reviews.router)
    app.include_router(inspect.router)
    app.include_router(webhooks.router)
    return app


app = create_app()
