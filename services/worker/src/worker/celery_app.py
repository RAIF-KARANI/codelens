"""Celery application. Run one worker per queue (planning.md §5.10, D88):

celery -A worker.celery_app worker -Q index -c 2
celery -A worker.celery_app worker -Q gpu -c 1
"""

from celery import Celery

from core import jobs
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
    task_routes=jobs.TASK_ROUTES,
    task_default_queue=jobs.DEFAULT_QUEUE,
    # Ack after the task finishes, so a crashed worker's job gets redelivered.
    # Every task is therefore idempotent: indexing writes only what changed,
    # embedding only stale nodes, and a review restarts its own row.
    task_acks_late=True,
    # Fetch one job at a time: indexing jobs are long, so prefetching would
    # let one worker hoard work while others sit idle.
    worker_prefetch_multiplier=1,
)
