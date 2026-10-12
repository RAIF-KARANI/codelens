"""Enqueueing background jobs.

The API sends tasks *by name* (core.jobs) and never imports worker code. The
API image doesn't contain it, and the API shouldn't load tree-sitter or model
clients to put a message on a queue. Routes depend on the `JobQueue`
protocol, so tests can record jobs (or run them in-process) without a broker.
"""

import asyncio
from collections.abc import Callable
from typing import Protocol

from celery import Celery, chain

from core import jobs


class QueueUnavailable(Exception):
    """The broker could not be reached; nothing was enqueued."""


class JobQueue(Protocol):
    async def index_repo(self, repo_id: int) -> None: ...

    async def review(self, review_id: int, base: str, *, title: str | None = None) -> None: ...


class CeleryJobQueue:
    def __init__(self, broker_url: str) -> None:
        # A producer-only client: no result backend, no task modules.
        self._celery = Celery("codelens-api", broker=broker_url)
        self._celery.conf.update(
            task_serializer="json",
            task_routes=jobs.TASK_ROUTES,
            task_default_queue=jobs.DEFAULT_QUEUE,
            # Fail fast when Redis is down instead of retrying while the HTTP
            # request hangs.
            broker_connection_timeout=2,
            task_publish_retry_policy={"max_retries": 2, "interval_start": 0.2},
        )

    async def index_repo(self, repo_id: int) -> None:
        # Index, then embed whatever changed. A chain runs the embed step only
        # after the index succeeds, and each step goes to its own queue.
        # immutable: embed takes repo_id, not the index task's return value.
        job = chain(
            self._celery.signature(jobs.INDEX_REPO, args=(repo_id,)),
            self._celery.signature(jobs.EMBED_REPO, args=(repo_id,), immutable=True),
        )
        await self._send(job.apply_async)

    async def review(self, review_id: int, base: str, *, title: str | None = None) -> None:
        await self._send(
            lambda: self._celery.send_task(
                jobs.REVIEW, args=(review_id, base), kwargs={"title": title}
            )
        )

    async def _send(self, publish: Callable[[], object]) -> None:
        try:
            # Publishing is blocking network I/O: keep it off the event loop.
            await asyncio.to_thread(publish)
        except Exception as exc:  # kombu raises several types for "broker down"
            raise QueueUnavailable(str(exc)) from exc
