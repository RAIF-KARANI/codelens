"""FastAPI application entrypoint: `uvicorn api.main:app`."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from api.errors import install_error_handlers
from api.middleware import RequestLog
from api.queue import CeleryJobQueue
from api.routes import health, inspect, repos, reviews, stats, webhooks
from core.config import Settings, get_settings
from core.db import create_engine, create_session_factory
from core.logs import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Shared clients are created once per process, not per request. None of
    # them connects eagerly, so the API still boots (and /health reports the
    # problem) when Postgres or Redis is down.
    settings: Settings = app.state.settings
    app.state.engine = create_engine(
        settings.database_url, transaction_pooler=settings.database_transaction_pooler
    )
    app.state.session_factory = create_session_factory(app.state.engine)
    if settings.demo_mode:
        # A read-only demo runs on Postgres alone (planning.md §15): nothing
        # is queued, and event streams read the database on their own.
        app.state.redis = app.state.events_redis = app.state.queue = None
    else:
        app.state.redis = Redis.from_url(
            settings.redis_url, socket_connect_timeout=2, socket_timeout=2
        )
        app.state.events_redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        app.state.queue = CeleryJobQueue(settings.redis_url)
    try:
        yield
    finally:
        for client in (app.state.redis, app.state.events_redis):
            if client is not None:
                await client.aclose()
        await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    # A factory lets tests build an app with their own settings, and gives one
    # obvious place to register routers.
    app = FastAPI(title="CodeLens API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    # The API's only outgoing HTTP is /health asking Ollama for its models, and
    # compose probes /health every 10 s: httpx's line per request is noise here.
    # (The workers keep it: there it shows each GitHub and model call.)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app.add_middleware(RequestLog)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(repos.router)
    app.include_router(reviews.router)
    app.include_router(stats.router)
    app.include_router(inspect.router)
    app.include_router(webhooks.router)
    return app


app = create_app()
