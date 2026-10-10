"""indexing: ordinals, file metadata, index_runs

Revision ID: 4130040c4b1d
Revises: f9a05e4be254
Create Date: 2026-10-10 16:40:37.328047+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4130040c4b1d"
down_revision: str | None = "f9a05e4be254"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "index_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="running", nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "stats", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name=op.f("ck_index_runs_index_run_status"),
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name=op.f("fk_index_runs_repo_id_repos"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_runs")),
    )
    op.create_index(op.f("ix_index_runs_repo_id"), "index_runs", ["repo_id"], unique=False)

    # Node identity for incremental re-indexing. Existing rows get ordinal 0,
    # which is only safe because no data has been indexed before this migration.
    op.add_column(
        "code_nodes", sa.Column("ordinal", sa.SmallInteger(), server_default="0", nullable=False)
    )
    op.create_unique_constraint(
        op.f("uq_code_nodes_file_id_qualified_name_ordinal"),
        "code_nodes",
        ["file_id", "qualified_name", "ordinal"],
    )
    # Redundant now: the unique constraint's index leads with file_id.
    op.drop_index(op.f("ix_code_nodes_file_id"), table_name="code_nodes")

    op.add_column(
        "files", sa.Column("is_test", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column(
        "files", sa.Column("size_bytes", sa.Integer(), server_default="0", nullable=False)
    )

    op.add_column(
        "repos",
        sa.Column("source", sa.String(length=32), server_default="github", nullable=False),
    )
    # Explicit: add_column doesn't create a non-native Enum's CHECK constraint.
    op.create_check_constraint(
        op.f("ck_repos_repo_source"), "repos", "source IN ('github', 'local')"
    )
    op.add_column("repos", sa.Column("local_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("repos", "local_path")
    op.drop_column("repos", "source")  # drops its CHECK constraint too
    op.drop_column("files", "size_bytes")
    op.drop_column("files", "is_test")
    op.create_index(op.f("ix_code_nodes_file_id"), "code_nodes", ["file_id"], unique=False)
    op.drop_constraint(
        op.f("uq_code_nodes_file_id_qualified_name_ordinal"), "code_nodes", type_="unique"
    )
    op.drop_column("code_nodes", "ordinal")
    op.drop_index(op.f("ix_index_runs_repo_id"), table_name="index_runs")
    op.drop_table("index_runs")
