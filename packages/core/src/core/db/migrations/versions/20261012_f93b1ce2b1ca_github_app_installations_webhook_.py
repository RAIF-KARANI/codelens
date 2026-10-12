"""github app: installations, webhook deliveries

Revision ID: f93b1ce2b1ca
Revises: df9649628e74
Create Date: 2026-10-12 17:17:00.670224+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f93b1ce2b1ca"
down_revision: str | None = "df9649628e74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "installations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("github_installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.Text(), nullable=False),
        sa.Column("account_type", sa.Text(), nullable=False),
        sa.Column("repository_selection", sa.Text(), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_installations")),
        sa.UniqueConstraint(
            "github_installation_id", name=op.f("uq_installations_github_installation_id")
        ),
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("delivery_id", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("installation_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("delivery_id", name=op.f("pk_webhook_deliveries")),
    )
    op.add_column("repos", sa.Column("installation_id", sa.BigInteger(), nullable=True))
    op.create_index(op.f("ix_repos_installation_id"), "repos", ["installation_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_repos_installation_id_installations"),
        "repos",
        "installations",
        ["installation_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_repos_installation_id_installations"), "repos", type_="foreignkey")
    op.drop_index(op.f("ix_repos_installation_id"), table_name="repos")
    op.drop_column("repos", "installation_id")
    op.drop_table("webhook_deliveries")
    op.drop_table("installations")
