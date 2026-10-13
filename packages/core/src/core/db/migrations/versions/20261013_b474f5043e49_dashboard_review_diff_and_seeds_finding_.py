"""dashboard: review diff and seeds, finding feedback

Revision ID: b474f5043e49
Revises: e53885814355
Create Date: 2026-10-13 05:59:30.079793+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b474f5043e49"
down_revision: str | None = "e53885814355"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "finding_feedback",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("finding_id", sa.BigInteger(), nullable=False),
        sa.Column("github_user_id", sa.Text(), nullable=False),
        sa.Column("vote", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("vote IN (-1, 1)", name=op.f("ck_finding_feedback_vote")),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_finding_feedback_finding_id_findings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finding_feedback")),
        sa.UniqueConstraint(
            "finding_id",
            "github_user_id",
            name=op.f("uq_finding_feedback_finding_id_github_user_id"),
        ),
    )
    op.add_column("reviews", sa.Column("diff", sa.Text(), nullable=True))
    op.add_column(
        "reviews", sa.Column("seeds", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("reviews", "seeds")
    op.drop_column("reviews", "diff")
    op.drop_table("finding_feedback")
