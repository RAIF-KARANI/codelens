"""dashboard: review title

The second Phase 6 migration (D114): a review keeps what its change is called
(a pull request's title, a manual review's branch), so the dashboard can show
it (Q19). Older reviews stay NULL.

Revision ID: cd53acf29082
Revises: b474f5043e49
Create Date: 2026-10-13 07:59:15.678412+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cd53acf29082"
down_revision: str | None = "b474f5043e49"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reviews", sa.Column("title", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("reviews", "title")
