from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base, CreatedAtMixin, IdMixin, str_enum
from core.enums import ReviewStatus, Severity


class Review(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "reviews"
    __table_args__ = (Index("ix_reviews_repo_id_pr_number", "repo_id", "pr_number"),)

    repo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("repos.id", ondelete="CASCADE"))
    pr_number: Mapped[int]
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

    review: Mapped[Review] = relationship(back_populates="findings", lazy="raise")
