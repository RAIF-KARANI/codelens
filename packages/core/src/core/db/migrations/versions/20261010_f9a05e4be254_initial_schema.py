"""initial schema

Revision ID: f9a05e4be254
Revises:
Create Date: 2026-10-10 11:45:36.944960+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from core.config import get_settings

revision: str = "f9a05e4be254"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The vector dimension is read from EMBED_DIM when this migration runs and is
# then fixed in the schema. Changing it later needs a new migration that
# alters the columns (and re-embeds everything).
EMBED_DIM = get_settings().embed_dim


def upgrade() -> None:
    # pgvector ships in the pgvector/pgvector image but must be enabled per database.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "repos",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.Text(), server_default="main", nullable=False),
        sa.Column("last_indexed_sha", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "status IN ('pending', 'indexing', 'ready', 'failed')",
            name=op.f("ck_repos_repo_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repos")),
        sa.UniqueConstraint("full_name", name=op.f("uq_repos_full_name")),
        sa.UniqueConstraint("github_id", name=op.f("uq_repos_github_id")),
    )
    op.create_table(
        "anti_patterns",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("example_code", sa.Text(), nullable=True),
        sa.Column(
            "severity",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=True),
        sa.Column("occurrences", sa.Integer(), server_default="0", nullable=False),
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
        sa.CheckConstraint(
            "severity IN ('critical', 'warning', 'suggestion')",
            name=op.f("ck_anti_patterns_anti_pattern_severity"),
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["repos.id"],
            name=op.f("fk_anti_patterns_repo_id_repos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anti_patterns")),
    )
    op.create_index(
        "ix_anti_patterns_embedding_hnsw",
        "anti_patterns",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(op.f("ix_anti_patterns_repo_id"), "anti_patterns", ["repo_id"], unique=False)
    op.create_table(
        "files",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name=op.f("fk_files_repo_id_repos"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_files")),
        sa.UniqueConstraint("repo_id", "path", name=op.f("uq_files_repo_id_path")),
    )
    op.create_table(
        "reviews",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("token_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name=op.f("ck_reviews_review_status"),
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name=op.f("fk_reviews_repo_id_repos"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
    )
    op.create_index(
        "ix_reviews_repo_id_pr_number", "reviews", ["repo_id", "pr_number"], unique=False
    )
    op.create_table(
        "code_nodes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("qualified_name", sa.Text(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("docstring", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("centrality", sa.Double(), nullable=True),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=True),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('module', 'class', 'function', 'method')",
            name=op.f("ck_code_nodes_node_kind"),
        ),
        sa.CheckConstraint("end_line >= start_line", name=op.f("ck_code_nodes_line_range")),
        sa.ForeignKeyConstraint(
            ["file_id"], ["files.id"], name=op.f("fk_code_nodes_file_id_files"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name=op.f("fk_code_nodes_repo_id_repos"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_code_nodes")),
    )
    op.create_index(
        "ix_code_nodes_embedding_hnsw",
        "code_nodes",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(op.f("ix_code_nodes_file_id"), "code_nodes", ["file_id"], unique=False)
    op.create_index(
        "ix_code_nodes_repo_id_qualified_name",
        "code_nodes",
        ["repo_id", "qualified_name"],
        unique=False,
    )
    op.create_index(
        "ix_code_nodes_tsv", "code_nodes", ["tsv"], unique=False, postgresql_using="gin"
    )
    op.create_table(
        "findings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("review_id", sa.BigInteger(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=False),
        sa.Column("line_end", sa.Integer(), nullable=False),
        sa.Column(
            "severity",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("suggested_fix", sa.Text(), nullable=True),
        sa.Column(
            "evidence_node_ids",
            postgresql.ARRAY(sa.BigInteger()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'warning', 'suggestion')",
            name=op.f("ck_findings_finding_severity"),
        ),
        sa.CheckConstraint("line_end >= line_start", name=op.f("ck_findings_line_range")),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["reviews.id"],
            name=op.f("fk_findings_review_id_reviews"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
    )
    op.create_index(op.f("ix_findings_review_id"), "findings", ["review_id"], unique=False)
    op.create_table(
        "code_edges",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("src_node_id", sa.BigInteger(), nullable=False),
        sa.Column("dst_node_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("target_name", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('calls', 'imports', 'inherits', 'contains')",
            name=op.f("ck_code_edges_edge_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["dst_node_id"],
            ["code_nodes.id"],
            name=op.f("fk_code_edges_dst_node_id_code_nodes"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name=op.f("fk_code_edges_repo_id_repos"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["src_node_id"],
            ["code_nodes.id"],
            name=op.f("fk_code_edges_src_node_id_code_nodes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_code_edges")),
    )
    op.create_index(op.f("ix_code_edges_dst_node_id"), "code_edges", ["dst_node_id"], unique=False)
    op.create_index(op.f("ix_code_edges_src_node_id"), "code_edges", ["src_node_id"], unique=False)
    op.create_index(
        "ix_code_edges_unresolved",
        "code_edges",
        ["repo_id", "target_name"],
        unique=False,
        postgresql_where=sa.text("dst_node_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_code_edges_unresolved",
        table_name="code_edges",
        postgresql_where=sa.text("dst_node_id IS NULL"),
    )
    op.drop_index(op.f("ix_code_edges_src_node_id"), table_name="code_edges")
    op.drop_index(op.f("ix_code_edges_dst_node_id"), table_name="code_edges")
    op.drop_table("code_edges")
    op.drop_index(op.f("ix_findings_review_id"), table_name="findings")
    op.drop_table("findings")
    op.drop_index("ix_code_nodes_tsv", table_name="code_nodes", postgresql_using="gin")
    op.drop_index("ix_code_nodes_repo_id_qualified_name", table_name="code_nodes")
    op.drop_index(op.f("ix_code_nodes_file_id"), table_name="code_nodes")
    op.drop_index(
        "ix_code_nodes_embedding_hnsw",
        table_name="code_nodes",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("code_nodes")
    op.drop_index("ix_reviews_repo_id_pr_number", table_name="reviews")
    op.drop_table("reviews")
    op.drop_table("files")
    op.drop_index(op.f("ix_anti_patterns_repo_id"), table_name="anti_patterns")
    op.drop_index(
        "ix_anti_patterns_embedding_hnsw",
        table_name="anti_patterns",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("anti_patterns")
    op.drop_table("repos")
    op.execute("DROP EXTENSION IF EXISTS vector")
