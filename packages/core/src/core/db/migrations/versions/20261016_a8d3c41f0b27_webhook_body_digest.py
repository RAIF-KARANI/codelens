"""webhook deliveries: body digest

Phase 9 step 8 (D134): the signature covers only the body, and dedupe was
keyed on the unsigned X-GitHub-Delivery header, so a captured delivery
re-sent under a new id was processed again. Each delivery now records the
SHA-256 of its signed body, unique, so the same body is handled once
whatever id it comes with. Older rows stay NULL.

Revision ID: a8d3c41f0b27
Revises: cd53acf29082
Create Date: 2026-10-16 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8d3c41f0b27"
down_revision: str | None = "cd53acf29082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("webhook_deliveries", sa.Column("body_sha256", sa.Text(), nullable=True))
    op.create_unique_constraint(
        op.f("uq_webhook_deliveries_body_sha256"), "webhook_deliveries", ["body_sha256"]
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_webhook_deliveries_body_sha256"), "webhook_deliveries", type_="unique"
    )
    op.drop_column("webhook_deliveries", "body_sha256")
