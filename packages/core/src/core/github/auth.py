"""Authenticating as the GitHub App, then as one of its installations.

A GitHub App has two identities:
- **The App itself:** a JWT signed with the App's private key (RS256), valid
  for at most 10 minutes. It can only manage the App: list installations and
  mint installation tokens.
- **An installation:** an account (user or org) that installed the App. Its
  token is minted with the App JWT, is scoped to the repos and permissions
  that account granted, and expires after an hour. All repo work (cloning,
  reading pull requests, posting reviews) uses installation tokens.

Tokens live only in process memory: never in the database, never in logs
(idea.md §12).
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt

from core.github.client import GitHubClient, InstallationToken

# GitHub rejects an App JWT whose exp is more than 10 minutes ahead.
APP_JWT_LIFETIME = timedelta(minutes=9)
# Issued slightly in the past, so a GitHub clock a little behind ours still
# accepts it (GitHub's own recommendation).
CLOCK_SKEW = timedelta(seconds=60)
# Mint a new installation token this long before the cached one expires, so a
# token never runs out in the middle of a clone or a review.
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def load_private_key(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(
            f"GitHub App private key not found at {path}: see docs/github-app.md"
        ) from exc


def app_jwt(app_id: int, private_key: str, *, now: datetime | None = None) -> str:
    now = now or _utcnow()
    claims = {"iat": now - CLOCK_SKEW, "exp": now + APP_JWT_LIFETIME, "iss": str(app_id)}
    return jwt.encode(claims, private_key, algorithm="RS256")


class InstallationTokens:
    """Installation tokens, each cached until shortly before it expires.

    No lock: two coroutines asking for the same installation at once may both
    mint a token, which is harmless (both are valid), while an asyncio lock
    would tie this cache to one event loop, and every Celery task runs its own
    (D7). For the same reason a worker passes a process-wide `cache` dict: a
    token outlives the task, but the HTTP client that minted it doesn't.
    """

    def __init__(
        self,
        client: GitHubClient,
        app_id: int,
        private_key: str,
        *,
        clock: Clock = _utcnow,
        cache: dict[int, InstallationToken] | None = None,
    ) -> None:
        self._client = client
        self._app_id = app_id
        self._private_key = private_key
        self._clock = clock
        self._cache = {} if cache is None else cache

    async def get(self, installation_id: int) -> str:
        now = self._clock()
        cached = self._cache.get(installation_id)
        if cached is not None and cached.expires_at - now > TOKEN_REFRESH_MARGIN:
            return cached.token
        minted = await self._client.create_installation_token(
            installation_id, app_jwt(self._app_id, self._private_key, now=now)
        )
        self._cache[installation_id] = minted
        return minted.token

    def forget(self, installation_id: int) -> None:
        """Drop a cached token (after a 401, or when the App is uninstalled)."""
        self._cache.pop(installation_id, None)
