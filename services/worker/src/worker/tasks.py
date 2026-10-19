"""Background jobs. Each task wraps one async core job in `asyncio.run()` with
a NullPool engine (D7): every task runs its own event loop, and a pooled
connection can't cross loops.

Tasks take ids, not objects: a message may wait in the queue, so the task
reads the current row when it runs. The tasks return small JSON summaries
(kept by the result backend, useful when debugging).
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from celery import signals
from celery.exceptions import SoftTimeLimitExceeded
from redis.asyncio import Redis
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from core import jobs
from core.config import Settings, get_settings
from core.db import create_engine
from core.db.models import Finding, IndexRun, Installation, Repo, Review
from core.diff.git import GitError, resolve
from core.embedding import embed_repository
from core.enums import (
    FindingStatus,
    IndexRunStatus,
    RepoSource,
    RepoStatus,
    ReviewSource,
    ReviewStatus,
)
from core.github import (
    GitHubClient,
    GitHubError,
    InstallationToken,
    InstallationTokens,
    load_private_key,
)
from core.github.mirror import BRANCHES, clone_url, mirror_path, sync_mirror
from core.github.publish import build_failure_note, build_review, failure_reason
from core.github.webhooks import Policy, rename_repository
from core.indexing import IndexingError, IndexLocked, IndexReport, index_repository, index_revision
from core.providers import open_providers
from core.providers.base import ProviderUnavailableError
from core.providers.ollama import OllamaClient
from core.review.config import PipelineConfig
from core.review.events import END, channel
from core.review.pipeline import ReviewError, ReviewResult, run_review
from core.review.recorder import Notify, append_event, fail_unfinished, ring
from worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# An index run holding the repo's lock usually finishes within minutes:
# retry every 30 s, for up to 30 minutes, before giving up.
LOCK_RETRY_SECONDS = 30
LOCK_MAX_RETRIES = 60

# Before a review starts, Ollama must answer (D181): after Windows starts,
# Docker brings the stack back before the user's Ollama is up, and the poller
# catches up at once. Waiting 30 s at a time, for up to 10 minutes, covers a
# start; the last try runs anyway, and fails with the usual note on the PR.
MODEL_WAIT_SECONDS = 30
MODEL_WAIT_TRIES = 20


# Unfinished reviews older than this aren't sent again when a worker starts:
# a push from last week has likely moved on.
UNFINISHED_WINDOW = timedelta(days=1)


class ModelNotAnswering(Exception):
    """Ollama didn't answer the check before a review: try again later."""


class PullRequestClosed(ReviewError):
    """A review tried again (D181) of a pull request closed since: nothing to
    review, and no note to leave on it."""


# Nodes per embed_repo run: ~20 s of GPU at the measured ~25 nodes/s, so a
# review never waits behind more than that (D107).
EMBED_SLICE_NODES = 512

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
    """Embed a repo's stale nodes, one slice per run (queue: gpu).

    The gpu queue is FIFO with one worker (D88), so one long job would hold
    back every review queued after it: a new 14k-node repo embeds for ~9
    minutes (Q17). Instead each run embeds at most EMBED_SLICE_NODES, then,
    if stale nodes remain, sends itself to the back of the queue. A review
    queued meanwhile runs before the next slice: it waits for at most one
    slice, ~20 s (D107)."""
    summary = asyncio.run(_embed(get_settings(), repo_id))
    if summary["remaining"]:
        requeue_embedding(repo_id)
    return summary


def requeue_embedding(repo_id: int) -> None:
    """The next slice goes to the back of the gpu queue. (A seam for tests,
    which run tasks in-process and have no worker to pick it up.)"""
    embed_repo.apply_async(args=(repo_id,))


@celery_app.task(name=jobs.REVIEW, bind=True, max_retries=MODEL_WAIT_TRIES)
def review(self: Any, review_id: int, base: str, title: str | None = None) -> dict[str, Any]:
    """Run a queued review: the head is the row's head_sha, compared with its
    merge-base with `base` (empty: the PR's base on GitHub now, for a review
    tried again, core.github.retry). A pull request's review is then posted on
    GitHub (queue: gpu). While Ollama doesn't answer, it waits, queued."""
    last_try = self.request.retries >= MODEL_WAIT_TRIES
    try:
        return asyncio.run(
            _review(
                get_settings(),
                review_id,
                base,
                title,
                wait_for_model=not last_try,
                first_wait=self.request.retries == 0,
            )
        )
    except ModelNotAnswering as exc:
        raise self.retry(exc=exc, countdown=MODEL_WAIT_SECONDS) from exc


@signals.worker_ready.connect
def _requeue_on_start(sender: Any, **_: Any) -> None:
    """A worker that takes reviews (the gpu queue) sends their jobs again
    when it starts (D181): Redis keeps nothing on disk, so a Windows restart
    loses the jobs of reviews queued, or waiting for Ollama, when Docker
    stopped, and their rows would wait for ever. A job sent twice is harmless:
    reviews run one at a time, and a finished one skips its second message."""
    consumed = getattr(sender.app.amqp.queues, "consume_from", None) or {}
    if jobs.GPU_QUEUE not in consumed:
        return
    try:
        ids = asyncio.run(_unfinished_reviews(get_settings()))
    except Exception:
        logger.warning("couldn't look for reviews to queue again", exc_info=True)
        return
    for review_id in ids:
        review.apply_async(args=(review_id, ""))
    if ids:
        logger.info("queued %d unfinished reviews again: %s", len(ids), ids)


async def _unfinished_reviews(settings: Settings) -> list[int]:
    """Pull request reviews queued or running in the last day: their base
    comes from GitHub again (an empty base), so only theirs can be re-sent."""
    engine = create_engine(settings.database_url, pooled=False)
    try:
        async with engine.connect() as conn:
            rows = await conn.scalars(
                select(Review.id)
                .where(
                    Review.source == ReviewSource.GITHUB,
                    Review.pr_number.is_not(None),
                    Review.status.in_([ReviewStatus.QUEUED, ReviewStatus.RUNNING]),
                    Review.created_at > func.now() - UNFINISHED_WINDOW,
                )
                .order_by(Review.id)
            )
            return list(rows)
    finally:
        await engine.dispose()


async def _model_answers(settings: Settings) -> bool:
    """One quick look, no retries: is the user's Ollama up?"""
    try:
        async with OllamaClient(settings.ollama_base_url, timeout_s=5, max_retries=0) as client:
            await client.version()
    except ProviderUnavailableError:
        return False
    return True


async def _index(settings: Settings, repo_id: int) -> dict[str, Any]:
    engine = create_engine(settings.database_url, pooled=False)
    try:
        repo = await _load_repo(engine, repo_id)
        last_run = await _latest_index_run(engine, repo_id)
        try:
            if repo.source is RepoSource.GITHUB:
                report = await _index_github(settings, engine, repo_id, repo)
            elif repo.local_path:
                report = await index_repository(engine, Path(repo.local_path), name=repo.full_name)
            else:
                raise IndexingError(f"{repo.full_name} has no local checkout to index")
        except IndexLocked:
            raise  # the run holding the lock sets the repo's status; the task retries
        except Exception as exc:
            await _fail_before_run(engine, repo_id, last_run, str(exc) or repr(exc))
            raise
    finally:
        await engine.dispose()
    return {
        "repo_id": repo_id,
        "index_run_id": report.index_run_id,
        "commit_sha": report.commit_sha,
        "wrote_graph": report.wrote_graph,
    }


async def _latest_index_run(engine: AsyncEngine, repo_id: int) -> int:
    async with engine.connect() as conn:
        latest = await conn.scalar(select(func.max(IndexRun.id)).where(IndexRun.repo_id == repo_id))
    return latest or 0


async def _fail_before_run(engine: AsyncEngine, repo_id: int, last_run: int, error: str) -> None:
    """Record a failure that came before the indexer opened a run: no
    checkout, a path that isn't a directory, no installation, a failed fetch.
    Once a run exists the indexer records its own failure (and sets the repo
    'failed'); before that nothing did, so a re-index's 'pending' claim, and
    the disabled button with it, stayed for good (D163). The failed run
    carries the error, so the Indexing tab says why.

    A run newer than `last_run` means the indexer got that far, and has
    already written how it ended."""
    async with engine.begin() as conn:
        opened = await conn.scalar(
            select(IndexRun.id).where(IndexRun.repo_id == repo_id, IndexRun.id > last_run)
        )
        if opened is not None:
            return
        await conn.execute(
            insert(IndexRun).values(
                repo_id=repo_id,
                status=IndexRunStatus.FAILED,
                finished_at=func.now(),
                error=error,
            )
        )
        await conn.execute(
            update(Repo)
            .where(Repo.id == repo_id)
            .values(status=RepoStatus.FAILED, updated_at=func.now())
        )


async def _index_github(
    settings: Settings, engine: AsyncEngine, repo_id: int, repo: Any
) -> IndexReport:
    """Refresh the repo's bare mirror, then index its default branch from git
    objects (no checkout; planning §5.1)."""
    if repo.github_installation_id is None:
        raise IndexingError(f"{repo.full_name} is not covered by any installation of the App")
    if not _covered(settings, repo):
        raise IndexingError(f"{repo.full_name}: {NOT_COVERED}")
    try:
        async with github_client(settings) as gh:
            token = await _installation_token(settings, gh, repo.github_installation_id)
            current = await _current_repo(engine, gh, token, repo_id, repo.github_id)
        default_branch, full_name = current["default_branch"], current["full_name"]
        mirror = mirror_path(settings.data_dir, repo_id)
        url = clone_url(settings.github_web_url, full_name)
        await asyncio.to_thread(sync_mirror, mirror, url, token)
    except (GitHubError, GitError) as exc:
        raise IndexingError(f"{repo.full_name}: {exc}") from exc
    async with engine.begin() as conn:
        await conn.execute(
            update(Repo).where(Repo.id == repo_id).values(default_branch=default_branch)
        )
    return await index_revision(engine, mirror, f"refs/heads/{default_branch}", name=full_name)


async def _current_repo(
    engine: AsyncEngine, gh: GitHubClient, token: str, repo_id: int, github_id: int | None
) -> dict[str, Any]:
    """The repository as GitHub has it now, looked up by its id, never by
    the stored name: a renamed repo's old name can come to belong to another
    repository, whose code would then be fetched into this one's mirror
    (D135). A changed name is stored."""
    if github_id is None:
        raise GitHubError(404, "the repo has no GitHub id", method="GET", path="/repositories")
    current = await gh.get_repository(github_id, token)
    async with engine.begin() as conn:
        await rename_repository(conn, current)
    return current


NOT_COVERED = "its account is no longer allowed, or the App's installation is suspended"


def _covered(settings: Settings, row: Any) -> bool:
    """The account policy webhooks apply, checked again before any token is
    minted: a job queued before an account was removed, or one queued another
    way (a re-index, or straight through Redis), does no work for it (D130)."""
    return Policy(settings.allowed_accounts).covers(
        row.account_login, row.account_id, row.suspended_at
    )


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
                engine,
                providers.embedder,
                repo.full_name,
                max_tokens=settings.embed_max_tokens,
                limit=EMBED_SLICE_NODES,
            )
    finally:
        await engine.dispose()
    remaining = report.nodes_total - report.up_to_date - report.embedded
    return {
        "repo_id": repo_id,
        "embedded": report.embedded,
        "up_to_date": report.up_to_date,
        "remaining": remaining,
    }


# A review in one of these states has nothing left to do.
_FINISHED = (ReviewStatus.COMPLETED, ReviewStatus.FAILED, ReviewStatus.SUPERSEDED)


async def _review(
    settings: Settings,
    review_id: int,
    base: str,
    title: str | None,
    *,
    wait_for_model: bool = False,
    first_wait: bool = True,
) -> dict[str, Any]:
    engine = create_engine(settings.database_url, pooled=False)
    try:
        row = await _load_review(engine, review_id)
        if row.status in _FINISHED:
            # A redelivered message for a review that already ran (acks are
            # late), or one a newer push superseded while it waited. Its
            # stream already ended.
            logger.info("review %s already %s; skipping", review_id, row.status)
            return {"review_id": review_id, "status": str(row.status), "skipped": True}
        if wait_for_model and not await _model_answers(settings):
            logger.info(
                "review %s: Ollama isn't answering; trying again in %d s",
                review_id,
                MODEL_WAIT_SECONDS,
            )
            if first_wait:
                async with engine.connect() as conn:
                    await append_event(conn, review_id, "waiting_for_model")
                    await conn.commit()
            raise ModelNotAnswering(f"review {review_id}: Ollama isn't answering")
        async with doorbell(settings) as notify:
            try:
                return await _run_review(settings, engine, row, review_id, base, title, notify)
            finally:
                # Whatever happened, nothing more will be written: tell
                # streamers to stop (D106).
                await _end(engine, review_id, notify)
    finally:
        await engine.dispose()


async def _run_review(
    settings: Settings,
    engine: AsyncEngine,
    row: Any,
    review_id: int,
    base: str,
    title: str | None,
    notify: Notify,
) -> dict[str, Any]:
    github = row.source is ReviewSource.GITHUB
    if github and await _supersede_if_stale(engine, row, review_id, "before it ran", notify):
        return {"review_id": review_id, "status": "superseded", "skipped": True}
    try:
        if github and not _covered(settings, row):
            raise ReviewError(f"{row.full_name}: {NOT_COVERED}")
        if github:
            try:
                repo_path, repo_name, pr_base = await _fetch_pull_request(
                    settings, engine, row, need_base=not base
                )
            except PullRequestClosed as closed:
                await _stand_down(engine, review_id, str(closed), notify)
                return {"review_id": review_id, "status": "superseded", "skipped": True}
            base = base or pr_base or ""
            if await _superseded_on_github(engine, repo_path, row, review_id, notify):
                return {"review_id": review_id, "status": "superseded", "skipped": True}
        elif row.local_path:
            repo_path, repo_name = Path(row.local_path), row.full_name
        else:
            raise ReviewError(f"{row.full_name} has no local checkout to review")
        config = PipelineConfig.from_yaml(settings.pipeline_config)
        async with open_providers(settings) as providers:
            result = await run_review(
                engine,
                providers,
                repo_path=repo_path,
                base=base,
                head=row.head_sha,
                repo_name=repo_name,
                config=config,
                context_window=settings.llm_num_ctx,
                source=row.source,
                review_id=review_id,
                title=title,
                notify=notify,
                final=False,  # posting may follow; _end closes the stream
            )
            model = providers.llm.model
    except Exception as exc:
        # The pipeline records its own failures; this also catches the ones
        # before it started (bad revision, missing config), and tells a
        # streamer why.
        # An Ollama outage is recorded as the pipeline records it, repr(exc),
        # which is what the poller looks for to try again (core.github.retry).
        message = repr(exc) if isinstance(exc, ProviderUnavailableError) else str(exc) or repr(exc)
        async with engine.connect() as conn:
            await fail_unfinished(conn, review_id, message)
            await append_event(conn, review_id, "error", message=message)
            await conn.commit()
        await ring(notify, review_id)
        if github:
            timed_out = isinstance(exc, SoftTimeLimitExceeded)
            reason = failure_reason(exc, timed_out=timed_out)
            await _post_failure(settings, engine, row, review_id, reason, notify)
        raise
    summary = {"review_id": review_id, "status": result.status.value}
    if github and result.status is ReviewStatus.COMPLETED:
        summary |= await _publish(settings, engine, row, review_id, result, model, notify)
    elif github and result.status is ReviewStatus.FAILED:
        reason = failure_reason(None, result.failed_chunks)
        await _post_failure(settings, engine, row, review_id, reason, notify)
    return {**summary, "posted": len(result.posted)}


async def _post_failure(
    settings: Settings, engine: AsyncEngine, row: Any, review_id: int, reason: str, notify: Notify
) -> None:
    """Tell the pull request its push wasn't reviewed, and why (closing v1's
    silent failures). Only for an installation CodeLens still acts for, and
    only while this is the PR's newest review: a later push is under way and
    will say what it found. Never raises: a note that can't be posted is
    logged, and the review's own failure stays what the task reports."""
    try:
        if row.github_installation_id is None or not _covered(settings, row):
            return
        async with engine.connect() as conn:
            newer = await conn.scalar(
                select(Review.id).where(
                    Review.repo_id == row.repo_id,
                    Review.pr_number == row.pr_number,
                    Review.id > review_id,
                )
            )
            full_name = await conn.scalar(select(Repo.full_name).where(Repo.id == row.repo_id))
        if newer is not None:
            return
        async with github_client(settings) as gh:
            token = await _installation_token(settings, gh, row.github_installation_id)
            posted = await gh.create_review(
                full_name, row.pr_number, build_failure_note(row.head_sha, reason), token
            )
        async with engine.connect() as conn:
            await conn.execute(
                update(Review).where(Review.id == review_id).values(github_review_id=posted["id"])
            )
            await append_event(
                conn, review_id, "failure_posted", github_review_id=posted["id"], reason=reason
            )
            await conn.commit()
        await ring(notify, review_id)
    except Exception:
        logger.warning("review %s: couldn't post its failure on the PR", review_id, exc_info=True)


async def _end(engine: AsyncEngine, review_id: int, notify: Notify) -> None:
    async with engine.connect() as conn:
        status = await conn.scalar(select(Review.status).where(Review.id == review_id))
        await append_event(conn, review_id, END, status=str(status))
        await conn.commit()
    await ring(notify, review_id)


@asynccontextmanager
async def doorbell(settings: Settings) -> AsyncIterator[Notify]:
    """Rings a review's Redis channel after each new event (D105). A client
    per task: like the database engine, it belongs to the task's event loop."""
    client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)

    async def notify(review_id: int) -> None:
        await client.publish(channel(review_id), "new")

    try:
        yield notify
    finally:
        await client.aclose()


async def _load_review(engine: AsyncEngine, review_id: int) -> Any:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    Review.status,
                    Review.head_sha,
                    Review.source,
                    Review.pr_number,
                    Review.repo_id,
                    Repo.full_name,
                    Repo.github_id,
                    Repo.local_path,
                    Installation.github_installation_id,
                    Installation.account_login,
                    Installation.account_id,
                    Installation.suspended_at,
                )
                .join(Repo, Repo.id == Review.repo_id)
                .outerjoin(Installation, Installation.id == Repo.installation_id)
                .where(Review.id == review_id)
            )
        ).first()
    if row is None:
        raise ReviewError(f"no review with id {review_id}")
    return row


async def _stand_down(engine: AsyncEngine, review_id: int, why: str, notify: Notify) -> None:
    """Not reviewed, and not a failure: marked superseded, with why."""
    async with engine.begin() as conn:
        await conn.execute(
            update(Review)
            .where(Review.id == review_id)
            .values(status=ReviewStatus.SUPERSEDED, finished_at=func.now(), error=why)
        )
        await append_event(conn, review_id, "superseded", why=why)
    await ring(notify, review_id)


async def _supersede_if_stale(
    engine: AsyncEngine, row: Any, review_id: int, when: str, notify: Notify
) -> bool:
    """A newer push to the same pull request (a later, not failed review)
    makes this head stale: skip it, or don't post it (D103)."""
    async with engine.begin() as conn:
        newer = await conn.scalar(
            select(Review.id).where(
                Review.repo_id == row.repo_id,
                Review.pr_number == row.pr_number,
                Review.id > review_id,
                Review.status.not_in([ReviewStatus.FAILED, ReviewStatus.SUPERSEDED]),
            )
        )
        if newer is None:
            return False
        await conn.execute(
            update(Review)
            .where(Review.id == review_id)
            .values(
                status=ReviewStatus.SUPERSEDED,
                finished_at=func.now(),
                error=f"superseded by review {newer} {when}",
            )
        )
        await append_event(conn, review_id, "superseded", by_review=newer, when=when)
    await ring(notify, review_id)
    return True


async def _superseded_on_github(
    engine: AsyncEngine, mirror: Path, row: Any, review_id: int, notify: Notify
) -> bool:
    """GitHub, not the order of our rows, says what the PR's head is: the
    fetch just brought refs/pull/N/head. A review of any other commit is
    stale, whether a replayed delivery or an event that came late queued it
    (D134). Nothing newer may be queued here to say so, so this is the check
    that holds when `_supersede_if_stale` finds nothing."""
    head = await asyncio.to_thread(resolve, mirror, f"refs/pull/{row.pr_number}/head")
    if head == row.head_sha:
        return False
    async with engine.begin() as conn:
        await conn.execute(
            update(Review)
            .where(Review.id == review_id)
            .values(
                status=ReviewStatus.SUPERSEDED,
                finished_at=func.now(),
                error=f"superseded: the pull request's head is now {head[:7]}",
            )
        )
        await append_event(conn, review_id, "superseded", head=head, when="before it ran")
    await ring(notify, review_id)
    return True


async def _fetch_pull_request(
    settings: Settings, engine: AsyncEngine, row: Any, *, need_base: bool = False
) -> tuple[Path, str, str | None]:
    """Bring the PR's head (refs/pull/N/head, which also covers PRs from
    forks) and the branches into the repo's mirror; returns the mirror, the
    repo's name as GitHub has it now, and, if asked, the PR's base now (read
    before the fetch, so the mirror has it)."""
    if row.github_installation_id is None:
        raise ReviewError(f"{row.full_name} is not covered by any installation of the App")
    mirror = mirror_path(settings.data_dir, row.repo_id)
    pull = f"+refs/pull/{row.pr_number}/head:refs/pull/{row.pr_number}/head"
    try:
        async with github_client(settings) as gh:
            token = await _installation_token(settings, gh, row.github_installation_id)
            current = await _current_repo(engine, gh, token, row.repo_id, row.github_id)
            base: str | None = None
            if need_base:
                pr = await gh.get_pull_request(current["full_name"], row.pr_number, token)
                if pr.get("state") != "open":
                    raise PullRequestClosed(
                        f"{current['full_name']}#{row.pr_number} is {pr.get('state')}"
                    )
                base = pr["base"]["sha"]
        url = clone_url(settings.github_web_url, current["full_name"])
        await asyncio.to_thread(sync_mirror, mirror, url, token, (BRANCHES, pull))
    except (GitHubError, GitError) as exc:
        raise ReviewError(f"{row.full_name}#{row.pr_number}: {exc}") from exc
    return mirror, current["full_name"], base


async def _publish(
    settings: Settings,
    engine: AsyncEngine,
    row: Any,
    review_id: int,
    result: ReviewResult,
    model: str,
    notify: Notify,
) -> dict[str, Any]:
    """Post the review on the pull request, unless a newer push came in while
    it ran. Findings on a function already commented on in this PR, and
    unchanged since (same topic, D104), are left out."""
    if await _supersede_if_stale(engine, row, review_id, "while it ran (not posted)", notify):
        return {"status": "superseded", "published": False}
    async with engine.connect() as conn:
        # As GitHub calls the repo now (the fetch looked it up by id, D135).
        full_name = await conn.scalar(select(Repo.full_name).where(Repo.id == row.repo_id))
        already = set(
            await conn.scalars(
                select(Finding.topic)
                .join(Review, Review.id == Finding.review_id)
                .where(
                    Review.repo_id == row.repo_id,
                    Review.pr_number == row.pr_number,
                    Review.github_review_id.is_not(None),
                    Finding.status == FindingStatus.POSTED,
                )
            )
        )
    publication = build_review(result, head_sha=row.head_sha, model=model, already_posted=already)
    async with github_client(settings) as gh:
        token = await _installation_token(settings, gh, row.github_installation_id)
        try:
            posted = await gh.create_review(full_name, row.pr_number, publication.payload, token)
        except GitHubError as exc:
            if exc.status != 422 or not publication.payload["comments"]:
                raise
            # GitHub refused an inline placement (a line it doesn't consider
            # part of the diff). Post again with every finding in the body.
            logger.warning("review %s: inline comments refused (%s); body only", review_id, exc)
            publication = build_review(
                result, head_sha=row.head_sha, model=model, already_posted=already, inline=False
            )
            posted = await gh.create_review(full_name, row.pr_number, publication.payload, token)
    async with engine.connect() as conn:
        await conn.execute(
            update(Review).where(Review.id == review_id).values(github_review_id=posted["id"])
        )
        await append_event(
            conn,
            review_id,
            "published",
            github_review_id=posted["id"],
            new=publication.new,
            repeated=publication.repeated,
            inline=publication.inline,
        )
        await conn.commit()
    await ring(notify, review_id)
    return {"published": True, "new": publication.new, "repeated": publication.repeated}


async def _load_repo(engine: AsyncEngine, repo_id: int) -> Any:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    Repo.full_name,
                    Repo.github_id,
                    Repo.source,
                    Repo.local_path,
                    Installation.github_installation_id,
                    Installation.account_login,
                    Installation.account_id,
                    Installation.suspended_at,
                )
                .outerjoin(Installation, Installation.id == Repo.installation_id)
                .where(Repo.id == repo_id)
            )
        ).first()
    if row is None:
        raise LookupError(f"no repo with id {repo_id}")
    return row
