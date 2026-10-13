"""Code nodes (the evidence drawer) and 👍/👎 on findings."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api import access, schemas
from api.auth import User, current_user, require_writable
from api.dependencies import get_session
from api.queries import votes
from core.db import models

router = APIRouter(prefix="/api/v1", tags=["inspect"], dependencies=[Depends(current_user)])

Session = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(current_user)]

feedback_t = models.FindingFeedback.__table__


@router.get("/nodes/{node_id}")
async def get_node(node_id: int, session: Session, user: CurrentUser) -> schemas.CodeNode:
    """A code node's source and metadata, e.g. a finding's cited evidence."""
    node, path = await access.node_for(session, user, node_id)
    return schemas.CodeNode(
        id=node.id,
        repo_id=node.repo_id,
        path=path,
        kind=node.kind.value,
        name=node.name,
        qualified_name=node.qualified_name,
        signature=node.signature,
        docstring=node.docstring,
        start_line=node.start_line,
        end_line=node.end_line,
        code=node.code,
    )


@router.post("/findings/{finding_id}/feedback", dependencies=[Depends(require_writable)])
async def vote(
    finding_id: int, body: schemas.FeedbackIn, session: Session, user: CurrentUser
) -> schemas.FeedbackOut:
    """👍 (1) or 👎 (-1). One vote per user per finding; voting again changes it."""
    await access.finding_for(session, user, finding_id)
    await session.execute(
        insert(feedback_t)
        .values(finding_id=finding_id, github_user_id=user.github_id, vote=body.vote)
        .on_conflict_do_update(
            index_elements=[feedback_t.c.finding_id, feedback_t.c.github_user_id],
            set_={"vote": body.vote, "updated_at": func.now()},
        )
    )
    await session.commit()
    return await _feedback(session, finding_id, user)


@router.delete(
    "/findings/{finding_id}/feedback",
    dependencies=[Depends(require_writable)],
    status_code=status.HTTP_200_OK,
)
async def unvote(finding_id: int, session: Session, user: CurrentUser) -> schemas.FeedbackOut:
    """Take back the caller's vote (nothing happens if there was none)."""
    await access.finding_for(session, user, finding_id)
    await session.execute(
        delete(feedback_t).where(
            feedback_t.c.finding_id == finding_id, feedback_t.c.github_user_id == user.github_id
        )
    )
    await session.commit()
    return await _feedback(session, finding_id, user)


async def _feedback(session: AsyncSession, finding_id: int, user: User) -> schemas.FeedbackOut:
    tallies, mine = await votes(session, [finding_id], user.github_id)
    return schemas.FeedbackOut(
        finding_id=finding_id, votes=tallies[finding_id], my_vote=mine.get(finding_id)
    )
