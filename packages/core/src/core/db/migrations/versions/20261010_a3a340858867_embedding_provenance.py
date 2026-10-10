"""embedding provenance

Revision ID: a3a340858867
Revises: 4130040c4b1d
Create Date: 2026-10-10 17:56:09.982938+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3a340858867"
down_revision: str | None = "4130040c4b1d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Which model produced each embedding and the hash of the text embedded,
    # so the embedding job can tell exactly which vectors are stale.
    op.add_column("anti_patterns", sa.Column("embedding_model", sa.Text(), nullable=True))
    op.add_column("anti_patterns", sa.Column("embedded_hash", sa.Text(), nullable=True))
    op.add_column("code_nodes", sa.Column("embedding_model", sa.Text(), nullable=True))
    op.add_column("code_nodes", sa.Column("embedded_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("code_nodes", "embedded_hash")
    op.drop_column("code_nodes", "embedding_model")
    op.drop_column("anti_patterns", "embedded_hash")
    op.drop_column("anti_patterns", "embedding_model")
