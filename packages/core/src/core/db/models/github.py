from datetime import datetime

from sqlalchemy import BigInteger, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base, IdMixin, TimestampMixin


class Installation(IdMixin, TimestampMixin, Base):
    """An account (user or organisation) that installed the GitHub App,
    mirrored from `installation` webhook events."""

    __tablename__ = "installations"

    github_installation_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    account_id: Mapped[int] = mapped_column(BigInteger)  # GitHub's id of the user/org
    account_login: Mapped[str]
    account_type: Mapped[str]  # "User" | "Organization" (GitHub's own values)
    repository_selection: Mapped[str | None]  # "all" | "selected"
    suspended_at: Mapped[datetime | None]  # set while the account has suspended the App


class WebhookDelivery(Base):
    """One webhook delivery, keyed by GitHub's delivery id and by its body, so
    a delivery that arrives twice is handled once."""

    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(primary_key=True)  # X-GitHub-Delivery (a GUID)
    event: Mapped[str]  # X-GitHub-Event: installation, push, pull_request, ...
    action: Mapped[str | None]  # payload "action": created, opened, synchronize, ...
    # GitHub's installation id, not a foreign key: the delivery log outlives
    # an uninstall.
    installation_id: Mapped[int | None] = mapped_column(BigInteger)
    # SHA-256 of the signed body. The signature doesn't cover the delivery id,
    # so the same body re-sent under a new id is the same delivery (D134).
    body_sha256: Mapped[str | None] = mapped_column(unique=True)
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # NULL until handled. Only a handled delivery counts as seen, so one that
    # failed halfway can be redelivered from GitHub's UI (D95).
    processed_at: Mapped[datetime | None]
