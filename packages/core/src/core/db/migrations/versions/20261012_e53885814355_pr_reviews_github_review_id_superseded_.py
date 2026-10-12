"""pr reviews: github review id, superseded status, finding topics

Revision ID: e53885814355
Revises: f93b1ce2b1ca
Create Date: 2026-10-12 17:59:23.621542+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e53885814355"
down_revision: str | None = "f93b1ce2b1ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Statuses are VARCHAR + CHECK (D4), which autogenerate doesn't compare: a new
# value means replacing the constraint by hand.
_OLD = "status IN ('queued', 'running', 'completed', 'failed')"
_NEW = "status IN ('queued', 'running', 'completed', 'failed', 'superseded')"


def upgrade() -> None:
    op.add_column("reviews", sa.Column("github_review_id", sa.BigInteger(), nullable=True))
    op.add_column("findings", sa.Column("topic", sa.Text(), nullable=True))
    op.drop_constraint(op.f("ck_reviews_review_status"), "reviews", type_="check")
    op.create_check_constraint(op.f("ck_reviews_review_status"), "reviews", _NEW)


def downgrade() -> None:
    # Superseded reviews can't satisfy the old constraint; they never ran or
    # posted, so they count as failed.
    op.execute("UPDATE reviews SET status = 'failed' WHERE status = 'superseded'")
    op.drop_constraint(op.f("ck_reviews_review_status"), "reviews", type_="check")
    op.create_check_constraint(op.f("ck_reviews_review_status"), "reviews", _OLD)
    op.drop_column("findings", "topic")
    op.drop_column("reviews", "github_review_id")
