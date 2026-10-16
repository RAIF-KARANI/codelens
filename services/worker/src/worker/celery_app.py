"""Celery application. Run one worker per queue (planning.md §5.10, D88):

celery -A worker.celery_app worker -Q index -c 2
celery -A worker.celery_app worker -Q gpu -c 1
"""

from typing import Any

from celery import Celery, Task, signals

from core import jobs
from core.config import get_settings
from core.logs import configure_logging, log_context

settings = get_settings()

# Every task's ceiling (D131). Measured: a review takes 9-49 s, an index of a
# 14k-node repo a few minutes, an embed slice ~20 s (D107), so 20 minutes is
# far past normal work.
TASK_SOFT_TIME_LIMIT_S = 20 * 60
TASK_TIME_LIMIT_S = 25 * 60


# Connecting to setup_logging tells Celery not to configure logging itself:
# its lines then come out in the same format as the API's (D121), at
# LOG_LEVEL (the CLI's --loglevel no longer applies).
@signals.setup_logging.connect
def _configure_logging(**_: Any) -> None:
    configure_logging(settings.log_level, settings.log_format)


# Each job's log lines carry its id and name, like a request's carry its id.
# The task runs in the thread that sent these signals, so the context reaches
# everything it logs, including inside its asyncio.run().
@signals.task_prerun.connect
def _enter_task(task_id: str, task: Task, **_: Any) -> None:
    log_context.set({"task_id": task_id, "task": task.name})


@signals.task_postrun.connect
def _leave_task(**_: Any) -> None:
    log_context.set(None)


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
    # A ceiling on every task (D131): parsing has budgets now, but nothing else
    # should hold one of the few shared worker slots for ever. The soft limit
    # raises in the task (a review records why it failed); the hard one kills
    # the child process. Neither is retried.
    task_soft_time_limit=TASK_SOFT_TIME_LIMIT_S,
    task_time_limit=TASK_TIME_LIMIT_S,
    # Fetch one job at a time: indexing jobs are long, so prefetching would
    # let one worker hoard work while others sit idle.
    worker_prefetch_multiplier=1,
)
