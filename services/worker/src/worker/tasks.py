"""Background jobs. Each task wraps one async core job in `asyncio.run()` with
a NullPool engine (D7): every task runs its own event loop, and a pooled
connection can't cross loops.

Tasks take ids, not objects: a message may wait in the queue, so the task
reads the current row when it runs. The tasks return small JSON summaries
(kept by the result backend, useful when debugging).
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from core import jobs
from core.config import Settings, get_settings
from core.db import create_engine
from core.db.models import Repo, Review
from core.embedding import embed_repository
from core.enums import RepoSource, ReviewStatus
from core.indexing import IndexingError, IndexLocked, index_repository
from core.providers import open_providers
from core.review.config import PipelineConfig
from core.review.pipeline import ReviewError, run_review
from core.review.recorder import fail_unfinished
from worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# An index run holding the repo's lock usually finishes within minutes:
# retry every 30 s, for up to 30 minutes, before giving up.
LOCK_RETRY_SECONDS = 30
LOCK_MAX_RETRIES = 60


@celery_app.task(name=jobs.PING)
def ping() -> str:
    """Smoke-test task: proves the broker round-trip works."""
    return "pong"


@celery_app.task(name=jobs.INDEX_REPO, bind=True, max_retries=LOCK_MAX_RETRIES)
def index_repo(self: Any, repo_id: int) -> dict[str, Any]:
    """Parse a repo and write what changed to its graph (queue: index)."""
    try:
        return asyncio.run(_index(get_settings(), repo_id))
    except IndexLocked as exc:
        # Another run holds the repo's advisory lock (D41, D89). A retry keeps
        # this task's place in its chain, so the embed step still follows.
        raise self.retry(exc=exc, countdown=LOCK_RETRY_SECONDS) from exc


@celery_app.task(name=jobs.EMBED_REPO)
def embed_repo(repo_id: int) -> dict[str, Any]:
    """Embed a repo's stale nodes (queue: gpu)."""
    return asyncio.run(_embed(get_settings(), repo_id))


@celery_app.task(name=jobs.REVIEW)
def review(review_id: int, base: str) -> dict[str, Any]:
    """Run a queued review: the head is the row's head_sha, compared with its
    merge-base with `base` (queue: gpu)."""
    return asyncio.run(_review(get_settings(), review_id, base))


async def _index(settings: Settings, repo_id: int) -> dict[str, Any]:
    engine = create_engine(settings.database_url, pooled=False)
    try:
        repo = await _load_repo(engine, repo_id)
        if repo.source is not RepoSource.LOCAL or not repo.local_path:
            # GitHub repos are indexed from a mirror clone (Phase 5 step 3).
            raise IndexingError(f"{repo.full_name} has no local checkout to index")
        report = await index_repository(engine, Path(repo.local_path), name=repo.full_name)
    finally:
        await engine.dispose()
    return {
        "repo_id": repo_id,
        "index_run_id": report.index_run_id,
        "commit_sha": report.commit_sha,
        "wrote_graph": report.wrote_graph,
    }


async def _embed(settings: Settings, repo_id: int) -> dict[str, Any]:
    engine = create_engine(settings.database_url, pooled=False)
    try:
        repo = await _load_repo(engine, repo_id)
        async with open_providers(settings) as providers:
            report = await embed_repository(
                engine, providers.embedder, repo.full_name, max_tokens=settings.embed_max_tokens
            )
    finally:
        await engine.dispose()
    return {"repo_id": repo_id, "embedded": report.embedded, "up_to_date": report.up_to_date}


async def _review(settings: Settings, review_id: int, base: str) -> dict[str, Any]:
    engine = create_engine(settings.database_url, pooled=False)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    select(
                        Review.status,
                        Review.head_sha,
                        Review.source,
                        Repo.full_name,
                        Repo.local_path,
                    )
                    .join(Repo, Repo.id == Review.repo_id)
                    .where(Review.id == review_id)
                )
            ).first()
        if row is None:
            raise ReviewError(f"no review with id {review_id}")
        if row.status in (ReviewStatus.COMPLETED, ReviewStatus.FAILED):
            # A redelivered message for a review that already ran: acks are late,
            # so a worker that dies after finishing can still get it again.
            logger.info("review %s already %s; skipping", review_id, row.status)
            return {"review_id": review_id, "status": str(row.status), "skipped": True}
        try:
            if not row.local_path:
                raise ReviewError(f"{row.full_name} has no local checkout to review")
            config = PipelineConfig.from_yaml(settings.pipeline_config)
            async with open_providers(settings) as providers:
                result = await run_review(
                    engine,
                    providers,
                    repo_path=Path(row.local_path),
                    base=base,
                    head=row.head_sha,
                    repo_name=row.full_name,
                    config=config,
                    context_window=settings.llm_num_ctx,
                    source=row.source,
                    review_id=review_id,
                )
        except Exception as exc:
            # Errors after the pipeline started are already recorded; this
            # catches the ones before it (bad revision, missing config).
            async with engine.connect() as conn:
                await fail_unfinished(conn, review_id, str(exc) or repr(exc))
            raise
    finally:
        await engine.dispose()
    return {"review_id": review_id, "status": result.status.value, "posted": len(result.posted)}


async def _load_repo(engine: AsyncEngine, repo_id: int) -> Any:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(Repo.full_name, Repo.source, Repo.local_path).where(Repo.id == repo_id)
            )
        ).first()
    if row is None:
        raise LookupError(f"no repo with id {repo_id}")
    return row
