from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base, CreatedAtMixin, IdMixin, TimestampMixin, str_enum
from core.enums import FindingStatus, ReviewSource, ReviewStatus, Severity


class Review(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "reviews"
    __table_args__ = (Index("ix_reviews_repo_id_pr_number", "repo_id", "pr_number"),)

    repo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("repos.id", ondelete="CASCADE"))
    source: Mapped[ReviewSource] = mapped_column(
        str_enum(ReviewSource, "review_source"), server_default=ReviewSource.GITHUB.value
    )
    pr_number: Mapped[int | None]  # NULL for CLI and eval reviews
    # What the change is called: the pull request's title when it was queued,
    # or the branch of a manual review. NULL for eval reviews, and for reviews
    # before Phase 6 step 4 (Q19).
    title: Mapped[str | None]
    base_sha: Mapped[str | None]  # the merge-base the diff was computed against
    head_sha: Mapped[str]
    status: Mapped[ReviewStatus] = mapped_column(
        str_enum(ReviewStatus, "review_status"), server_default=ReviewStatus.QUEUED.value
    )
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    model: Mapped[str | None]
    # JSONB rather than fixed columns: providers report different counters
    # (input/output/cached tokens, etc.).
    token_usage: Mapped[dict[str, int] | None] = mapped_column(JSONB)
    # The exact pipeline config used, so any review (and eval) is reproducible.
    pipeline_config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    error: Mapped[str | None]
    # The review CodeLens posted on the pull request, once posted. Findings of
    # posted reviews are what a later push's review checks for repeats.
    github_review_id: Mapped[int | None] = mapped_column(BigInteger)
    # What was reviewed, kept with the review (D110): the raw unified diff, and
    # the changed nodes [{path, qualified_name, kind, change, start_line,
    # end_line, signature_changed}]. NULL for reviews before Phase 6.
    diff: Mapped[str | None]
    seeds: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    findings: Mapped[list["Finding"]] = relationship(
        back_populates="review", lazy="raise", passive_deletes=True
    )


class Finding(IdMixin, Base):
    __tablename__ = "findings"
    __table_args__ = (CheckConstraint("line_end >= line_start", name="line_range"),)

    review_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("reviews.id", ondelete="CASCADE"), index=True
    )
    file_path: Mapped[str]
    line_start: Mapped[int]
    line_end: Mapped[int]
    severity: Mapped[Severity] = mapped_column(str_enum(Severity, "finding_severity"))
    category: Mapped[str]
    title: Mapped[str]
    explanation: Mapped[str]
    suggested_fix: Mapped[str | None]
    # The code_nodes the LLM was shown as evidence. An array can't carry foreign
    # keys, so these may point at deleted nodes after a re-index; that's
    # acceptable for a historical review record.
    evidence_node_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), server_default=text("'{}'")
    )
    confidence: Mapped[float | None]
    # Stable identity of "the same finding" across pushes, so a re-review
    # doesn't post it twice: file + category + the changed code it points at.
    fingerprint: Mapped[str | None]
    # The function the finding is about, by name and content (D104): a later
    # push of the same PR doesn't comment again on an unchanged function.
    topic: Mapped[str | None]
    status: Mapped[FindingStatus] = mapped_column(
        str_enum(FindingStatus, "finding_status"), server_default=FindingStatus.POSTED.value
    )
    drop_reason: Mapped[str | None]  # which validation rule rejected it

    review: Mapped[Review] = relationship(back_populates="findings", lazy="raise")


class FindingFeedback(IdMixin, TimestampMixin, Base):
    """A 👍 or 👎 from a dashboard user on one finding (one per user, changeable)."""

    __tablename__ = "finding_feedback"
    __table_args__ = (
        UniqueConstraint("finding_id", "github_user_id"),
        CheckConstraint("vote IN (-1, 1)", name="vote"),
    )

    finding_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("findings.id", ondelete="CASCADE")
    )
    github_user_id: Mapped[str]  # the JWT's `sub`
    vote: Mapped[int] = mapped_column(SmallInteger)


class ReviewContextItem(IdMixin, Base):
    """One retrieved candidate for one review chunk: what the LLM was (or
    wasn't) shown, with the scores that decided it. The "why was this context
    used?" trace, and raw material for retrieval evals."""

    __tablename__ = "review_context_items"

    review_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("reviews.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(SmallInteger)
    # SET NULL: the trace outlives a re-index that deletes the node.
    node_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("code_nodes.id", ondelete="SET NULL")
    )
    label: Mapped[str]  # the id the prompt used, e.g. "N12"
    qualified_name: Mapped[str]  # kept even if the node is later deleted
    stage_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    included: Mapped[bool]  # False = retrieved but didn't fit the budget
    truncated: Mapped[bool] = mapped_column(server_default="false")  # signature only
    token_cost: Mapped[int] = mapped_column(server_default="0")


class ReviewEvent(IdMixin, CreatedAtMixin, Base):
    """Timeline of a review (stage changes, progress, errors). Phase 5 streams
    these over SSE; `seq` lets a reconnecting client resume."""

    __tablename__ = "review_events"
    __table_args__ = (UniqueConstraint("review_id", "seq"),)

    review_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("reviews.id", ondelete="CASCADE"))
    seq: Mapped[int]
    type: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
