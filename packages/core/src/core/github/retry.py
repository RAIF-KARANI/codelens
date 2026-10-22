"""Reviewing a pull request's push again (planning-v2.md Phase 11 step 8, D181).

The friend's-laptop rehearsal found reviews that failed only because Ollama
wasn't answering yet: after Windows starts, Docker brings CodeLens's
containers back by itself, the poller catches up on what was pushed
meanwhile, and the review runs before the user's Ollama has started. The
worker now waits for Ollama first (worker.tasks); these are for what is left:

- `retry_review`: a failed review of a pull request, queued again for the same
  commit. The dashboard's Review again button (POST /reviews/{id}/retry), and
- `model_outages`: the failures that were only Ollama being down, which the
  poller queues again once Ollama answers (api.poller).

A retry is a new review row, like a push: the failed one stays in the history.
The worker looks the PR's base up on GitHub (an empty `base`), since a review
that failed early never computed one.
"""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from core.db.models import Installation, Repo, Review
from core.enums import ReviewSource, ReviewStatus
from core.github.webhooks import QueuedReview, reviews_started

reviews_t = Review.__table__
repos_t = Repo.__table__
installations_t = Installation.__table__

# How a review that failed because Ollama didn't answer is recorded: the
# pipeline (and the worker, for a failure before it) store repr(exc).
MODEL_OUTAGE = "ProviderUnavailableError("

# Ollama coming back queues a review again only if it failed this recently:
# a PR that failed last week has likely moved on, or been reviewed by hand.
OUTAGE_WINDOW = timedelta(hours=24)

# At most this many failed tries of one commit before the poller stops
# re-queuing it: an Ollama that answers a probe but fails reviews (a model
# that can't load, say) must not loop. The button still works.
MAX_OUTAGE_TRIES = 3


class RetryRefused(Exception):
    """Why a review can't be tried again; `code` is the API's error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_model_outage(error: str | None) -> bool:
    return bool(error and error.startswith(MODEL_OUTAGE))


async def retry_review(
    conn: AsyncConnection, review_id: int, *, reviews_per_hour: int = 0
) -> QueuedReview:
    """Queue a failed pull request review again, for the same commit. Only the
    PR's newest review can be: anything newer already reviews a later push, or
    this one again. Under the intake's per-PR lock, so a push arriving at the
    same moment doesn't queue a second review (D170).

    A retry counts against the account's GITHUB_REVIEWS_PER_HOUR like a push
    does (`reviews_per_hour`, 0 for no limit; D186). Without that, Review again
    on a review the limit refused queued it anyway."""
    row = (
        await conn.execute(
            select(
                reviews_t.c.repo_id,
                reviews_t.c.pr_number,
                reviews_t.c.source,
                reviews_t.c.status,
                reviews_t.c.head_sha,
                reviews_t.c.title,
            ).where(reviews_t.c.id == review_id)
        )
    ).first()
    if row is None:
        raise RetryRefused("not_found", "no such review")
    if row.source is not ReviewSource.GITHUB or row.pr_number is None:
        raise RetryRefused("not_retryable", "only a pull request's review can be tried again")
    if row.status is not ReviewStatus.FAILED:
        raise RetryRefused("not_retryable", f"the review is {row.status.value}, not failed")
    await conn.execute(
        select(
            func.pg_advisory_xact_lock(func.hashtext(f"pull_request:{row.repo_id}:{row.pr_number}"))
        )
    )
    newer = await conn.scalar(
        select(reviews_t.c.id).where(
            reviews_t.c.repo_id == row.repo_id,
            reviews_t.c.pr_number == row.pr_number,
            reviews_t.c.id > review_id,
        )
    )
    if newer is not None:
        raise RetryRefused(
            "not_retryable", f"review #{newer} is newer: it reviews this pull request"
        )
    if reviews_per_hour:
        account_id = await conn.scalar(
            select(installations_t.c.account_id).where(
                repos_t.c.id == row.repo_id, repos_t.c.installation_id == installations_t.c.id
            )
        )
        # A repo without an installation has nothing to count against; the
        # worker refuses to review it anyway (it isn't covered).
        if account_id is not None:
            same_pr = (reviews_t.c.repo_id == row.repo_id) & (
                reviews_t.c.pr_number == row.pr_number
            )
            started = await reviews_started(conn, account_id, besides=same_pr)
            if started >= reviews_per_hour:
                raise RetryRefused(
                    "over_limit",
                    f"the account started {started} reviews in the last hour, the limit"
                    f" (GITHUB_REVIEWS_PER_HOUR={reviews_per_hour}); try again later",
                )
    new_id = await conn.scalar(
        insert(reviews_t)
        .values(
            repo_id=row.repo_id,
            source=ReviewSource.GITHUB,
            pr_number=row.pr_number,
            title=row.title,
            head_sha=row.head_sha,
            status=ReviewStatus.QUEUED,
        )
        .returning(reviews_t.c.id)
    )
    return QueuedReview(new_id, "", row.title)


async def model_outages(conn: AsyncConnection) -> list[int]:
    """Failed pull request reviews worth queuing again now that Ollama answers:
    failed because it didn't, recently, the newest review of their PR, and
    not already tried MAX_OUTAGE_TRIES times at that commit."""
    newest = (
        select(func.max(reviews_t.c.id))
        .where(reviews_t.c.source == ReviewSource.GITHUB, reviews_t.c.pr_number.is_not(None))
        .group_by(reviews_t.c.repo_id, reviews_t.c.pr_number)
    )
    candidates = (
        await conn.execute(
            select(reviews_t.c.id, reviews_t.c.repo_id, reviews_t.c.pr_number, reviews_t.c.head_sha)
            .where(
                reviews_t.c.id.in_(newest),
                reviews_t.c.status == ReviewStatus.FAILED,
                reviews_t.c.error.startswith(MODEL_OUTAGE),
                reviews_t.c.finished_at > func.now() - OUTAGE_WINDOW,
            )
            .order_by(reviews_t.c.id)
        )
    ).all()
    worth: list[int] = []
    for row in candidates:
        tries = await conn.scalar(
            select(func.count()).where(
                reviews_t.c.repo_id == row.repo_id,
                reviews_t.c.pr_number == row.pr_number,
                reviews_t.c.head_sha == row.head_sha,
                reviews_t.c.status == ReviewStatus.FAILED,
                reviews_t.c.error.startswith(MODEL_OUTAGE),
            )
        )
        if tries < MAX_OUTAGE_TRIES:
            worth.append(row.id)
    return worth
