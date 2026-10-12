"""Webhook deliveries: proving one came from GitHub, and recording what it
says about installations.

GitHub signs every delivery with the App's webhook secret:
`X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw body>`. Anyone can
POST to our URL, so a delivery whose signature doesn't match is rejected
before its body is even parsed.
"""

import hashlib
import hmac
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from core.db.models import Installation

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"

installations_t = Installation.__table__


def sign(secret: str, body: bytes) -> str:
    """The signature header value GitHub would send for `body`."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    # compare_digest takes the same time wherever the strings differ, so an
    # attacker can't find the right signature byte by byte by timing replies.
    return hmac.compare_digest(sign(secret, body), header)


async def apply_installation_event(
    conn: AsyncConnection, action: str | None, payload: dict[str, Any]
) -> None:
    """Mirror an `installation` event into the installations table.

    Every action except `deleted` carries the installation's current state
    (account, repository selection, suspended_at), so they all upsert it:
    created, suspend, unsuspend, new_permissions_accepted. `deleted` removes
    the row; repos that belonged to it keep their data with installation_id
    set to NULL (what happens to them is step 3's business).
    """
    installation = payload["installation"]
    if action == "deleted":
        await conn.execute(
            delete(installations_t).where(
                installations_t.c.github_installation_id == installation["id"]
            )
        )
        return
    account = installation["account"]
    values = {
        "account_id": account["id"],
        "account_login": account["login"],
        "account_type": account["type"],
        "repository_selection": installation.get("repository_selection"),
        "suspended_at": _timestamp(installation.get("suspended_at")),
    }
    await conn.execute(
        insert(installations_t)
        .values(github_installation_id=installation["id"], **values)
        .on_conflict_do_update(
            index_elements=[installations_t.c.github_installation_id],
            set_={**values, "updated_at": func.now()},
        )
    )


def _timestamp(value: str | None) -> datetime | None:
    # GitHub sends ISO 8601 ("2026-10-12T17:00:00Z"); asyncpg wants a datetime.
    return datetime.fromisoformat(value) if value else None
