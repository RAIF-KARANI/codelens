"""Celery application: `celery -A worker.celery_app worker`."""

from celery import Celery

from core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "codelens",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Ack after the task finishes, so a crashed worker's job gets redelivered.
    # Indexing jobs must therefore be idempotent (which step 4 guarantees).
    task_acks_late=True,
    # Fetch one job at a time: indexing jobs are long, so prefetching would
    # let one worker hoard work while others sit idle.
    worker_prefetch_multiplier=1,
)
