"""review pipeline: trace, events, finding status

Revision ID: df9649628e74
Revises: a3a340858867
Create Date: 2026-10-10 18:25:35.551072+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "df9649628e74"
down_revision: str | None = "a3a340858867"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("review_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["reviews.id"],
            name=op.f("fk_review_events_review_id_reviews"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_events")),
        sa.UniqueConstraint("review_id", "seq", name=op.f("uq_review_events_review_id_seq")),
    )
    op.create_table(
        "review_context_items",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("review_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_index", sa.SmallInteger(), nullable=False),
        sa.Column("node_id", sa.BigInteger(), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("qualified_name", sa.Text(), nullable=False),
        sa.Column(
            "stage_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("token_cost", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["code_nodes.id"],
            name=op.f("fk_review_context_items_node_id_code_nodes"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["reviews.id"],
            name=op.f("fk_review_context_items_review_id_reviews"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_context_items")),
    )
    op.create_index(
        op.f("ix_review_context_items_review_id"),
        "review_context_items",
        ["review_id"],
        unique=False,
    )
    op.add_column("findings", sa.Column("confidence", sa.Double(), nullable=True))
    op.add_column("findings", sa.Column("fingerprint", sa.Text(), nullable=True))
    op.add_column(
        "findings",
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="posted",
            nullable=False,
        ),
    )
    op.add_column("findings", sa.Column("drop_reason", sa.Text(), nullable=True))
    # add_column doesn't create a non-native Enum's CHECK constraint, so add them explicitly.
    op.create_check_constraint(
        op.f("ck_findings_finding_status"), "findings", "status IN ('posted', 'dropped')"
    )
    op.add_column(
        "reviews",
        sa.Column(
            "source",
            sa.String(length=32),
            server_default="github",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_reviews_review_source"), "reviews", "source IN ('github', 'cli', 'eval')"
    )
    op.add_column("reviews", sa.Column("base_sha", sa.Text(), nullable=True))
    op.add_column(
        "reviews",
        sa.Column(
            "pipeline_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.add_column("reviews", sa.Column("error", sa.Text(), nullable=True))
    # CLI and eval reviews have no PR number.
    op.alter_column("reviews", "pr_number", existing_type=sa.INTEGER(), nullable=True)


def downgrade() -> None:
    # Reviews without a PR number (CLI/eval) can't exist in the old schema.
    op.execute("DELETE FROM reviews WHERE pr_number IS NULL")
    op.alter_column("reviews", "pr_number", existing_type=sa.INTEGER(), nullable=False)
    op.drop_column("reviews", "error")
    op.drop_column("reviews", "pipeline_config")
    op.drop_column("reviews", "base_sha")
    op.drop_column("reviews", "source")
    op.drop_column("findings", "drop_reason")
    op.drop_column("findings", "status")
    op.drop_column("findings", "fingerprint")
    op.drop_column("findings", "confidence")
    op.drop_index(op.f("ix_review_context_items_review_id"), table_name="review_context_items")
    op.drop_table("review_context_items")
    op.drop_table("review_events")
