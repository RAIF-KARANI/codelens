"""Read helpers shared by the routes."""

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import schemas
from core.db import models
from core.enums import FindingStatus

# Pages are small: the dashboard lists, it doesn't export.
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class _HasId(Protocol):
    id: int


def page_of[T: _HasId](rows: Sequence[T], limit: int) -> tuple[list[T], int | None]:
    """Split rows fetched with LIMIT limit + 1, newest (highest id) first. The
    extra row only says whether another page exists; the cursor is the last id
    shown, and the next page is `WHERE id < cursor`. Unlike OFFSET, a page
    doesn't shift when new rows arrive."""
    items = list(rows[:limit])
    return items, (items[-1].id if len(rows) > limit else None)


async def severity_counts(
    session: AsyncSession, review_ids: Sequence[int]
) -> dict[int, schemas.SeverityCounts]:
    """Posted findings per severity, for each review, in one grouped query."""
    counts = {review_id: schemas.SeverityCounts() for review_id in review_ids}
    if not review_ids:
        return counts
    f = models.Finding
    rows = await session.execute(
        select(f.review_id, f.severity, func.count())
        .where(f.review_id.in_(review_ids), f.status == FindingStatus.POSTED)
        .group_by(f.review_id, f.severity)
    )
    for review_id, severity, n in rows:
        setattr(counts[review_id], severity.value, n)
    return counts


async def review_summaries(
    session: AsyncSession, reviews: Sequence[models.Review]
) -> list[schemas.ReviewSummary]:
    counts = await severity_counts(session, [r.id for r in reviews])
    return [
        schemas.ReviewSummary.model_validate(r).model_copy(update={"counts": counts[r.id]})
        for r in reviews
    ]


async def votes(
    session: AsyncSession, finding_ids: Sequence[int], github_user_id: str
) -> tuple[dict[int, schemas.Votes], dict[int, int]]:
    """👍/👎 counts per finding, and the caller's own votes, in two queries."""
    counts = {finding_id: schemas.Votes() for finding_id in finding_ids}
    mine: dict[int, int] = {}
    if not finding_ids:
        return counts, mine
    fb = models.FindingFeedback
    rows = await session.execute(
        select(fb.finding_id, fb.vote, func.count())
        .where(fb.finding_id.in_(finding_ids))
        .group_by(fb.finding_id, fb.vote)
    )
    for finding_id, vote, n in rows:
        if vote == 1:
            counts[finding_id].up = n
        else:
            counts[finding_id].down = n
    own = await session.execute(
        select(fb.finding_id, fb.vote).where(
            fb.finding_id.in_(finding_ids), fb.github_user_id == github_user_id
        )
    )
    mine = {finding_id: vote for finding_id, vote in own}
    return counts, mine
