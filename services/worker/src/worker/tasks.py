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

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from core import jobs
from core.config import Settings, get_settings
from core.db import create_engine
from core.db.models import Installation, Repo, Review
from core.diff.git import GitError
from core.embedding import embed_repository
from core.enums import RepoSource, ReviewStatus
from core.github import (
    GitHubClient,
    GitHubError,
    InstallationToken,
    InstallationTokens,
    load_private_key,
)
from core.github.mirror import clone_url, mirror_path, sync_mirror
from core.indexing import IndexingError, IndexLocked, IndexReport, index_repository, index_revision
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

# Installation tokens live an hour, longer than any task, so they are cached
# per worker process (D96). The HTTP client that mints them is per task.
_TOKEN_CACHE: dict[int, InstallationToken] = {}


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
        if repo.source is RepoSource.GITHUB:
            report = await _index_github(settings, engine, repo_id, repo)
        elif repo.local_path:
            report = await index_repository(engine, Path(repo.local_path), name=repo.full_name)
        else:
            raise IndexingError(f"{repo.full_name} has no local checkout to index")
    finally:
        await engine.dispose()
    return {
        "repo_id": repo_id,
        "index_run_id": report.index_run_id,
        "commit_sha": report.commit_sha,
        "wrote_graph": report.wrote_graph,
    }


async def _index_github(
    settings: Settings, engine: AsyncEngine, repo_id: int, repo: Any
) -> IndexReport:
    """Refresh the repo's bare mirror, then index its default branch from git
    objects (no checkout; planning §5.1)."""
    if repo.github_installation_id is None:
        raise IndexingError(f"{repo.full_name} is not covered by any installation of the App")
    try:
        async with github_client(settings) as gh:
            token = await _installation_token(settings, gh, repo.github_installation_id)
            default_branch = (await gh.get_repo(repo.full_name, token))["default_branch"]
        mirror = mirror_path(settings.data_dir, repo_id)
        url = clone_url(settings.github_web_url, repo.full_name)
        await asyncio.to_thread(sync_mirror, mirror, url, token)
    except (GitHubError, GitError) as exc:
        raise IndexingError(f"{repo.full_name}: {exc}") from exc
    async with engine.begin() as conn:
        await conn.execute(
            update(Repo).where(Repo.id == repo_id).values(default_branch=default_branch)
        )
    return await index_revision(engine, mirror, f"refs/heads/{default_branch}", name=repo.full_name)


async def _installation_token(settings: Settings, gh: GitHubClient, installation_id: int) -> str:
    if settings.github_app_id is None:
        raise IndexingError("the GitHub App is not configured: GITHUB_APP_ID is not set")
    key = load_private_key(settings.github_app_private_key_path)
    tokens = InstallationTokens(gh, settings.github_app_id, key, cache=_TOKEN_CACHE)
    return await tokens.get(installation_id)


def github_client(settings: Settings) -> GitHubClient:
    """A client per task: its connections belong to the task's event loop.
    (A seam for tests, which give it a mock transport.)"""
    return GitHubClient(settings.github_api_url)


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
                select(
                    Repo.full_name,
                    Repo.source,
                    Repo.local_path,
                    Installation.github_installation_id,
                )
                .outerjoin(Installation, Installation.id == Repo.installation_id)
                .where(Repo.id == repo_id)
            )
        ).first()
    if row is None:
        raise LookupError(f"no repo with id {repo_id}")
    return row
