"""Task definitions. Real jobs (clone, parse, embed) arrive in later phases."""

from worker.celery_app import celery_app


@celery_app.task(name="worker.ping")
def ping() -> str:
    """Smoke-test task: proves the broker round-trip works."""
    return "pong"
