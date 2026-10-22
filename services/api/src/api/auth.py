"""Who is calling. The dashboard's Next.js server sends
`Authorization: Bearer <JWT>`: HS256, signed with the shared API_JWT_SECRET,
5-minute expiry, `sub` = the GitHub user id, `login` = their handle
(planning.md §7).

What the caller may see (D111): the token's `installations` claim lists the
GitHub App installation ids its user can access (the dashboard asks GitHub,
GET /user/installations, at login), and the accounts in API_ADMIN_LOGINS see
every repo: by login, or pinned to the user id (`login:id`) that the token's
`sub` must then carry, since a login can change hands (D136). The checks
themselves are in api/access.py.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.dependencies import get_settings
from api.errors import ApiError
from core.config import Settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(minutes=5)

# D137 accepts per-installation visibility while the owner is the only one
# signed in. The first time anyone else is, the log says what they can see
# (D192, security audit run-2). Bounded, so it can't grow without end.
_NOTED_USERS: set[str] = set()
_MAX_NOTED = 1000

# auto_error=False: a missing header becomes our own 401 shape, not FastAPI's 403.
_bearer = HTTPBearer(auto_error=False)
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True, slots=True)
class User:
    github_id: str
    login: str
    installations: frozenset[int] = frozenset()  # GitHub installation ids it can access
    is_admin: bool = False  # sees every repo, including local and eval ones
    # A demo instance (DEMO_MODE) holds only exported public demo repos, so
    # every signed-in user sees all of them (D115).
    demo: bool = False


def issue_token(
    secret: str,
    github_id: str,
    login: str,
    *,
    installations: Sequence[int] = (),
    ttl: timedelta = TOKEN_TTL,
) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": github_id,
        "login": login,
        "installations": list(installations),
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    secret = settings.api_jwt_secret.get_secret_value()
    if not secret:
        # Fail closed: a server without a secret must not serve anyone.
        raise ApiError(503, "auth_not_configured", "API_JWT_SECRET is not set on the server")
    if credentials is None:
        raise ApiError(401, "unauthenticated", "missing bearer token", headers=_CHALLENGE)
    try:
        # algorithms is a fixed list: the token's own `alg` header is never
        # trusted (that is how "alg: none" forgeries work).
        claims = jwt.decode(
            credentials.credentials,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        # The exception class names the problem (ExpiredSignatureError, ...)
        # without echoing anything from the token.
        raise ApiError(401, "invalid_token", type(exc).__name__, headers=_CHALLENGE) from exc
    installations = claims.get("installations") or []
    if not isinstance(installations, list) or not all(
        isinstance(i, int) and not isinstance(i, bool) for i in installations
    ):
        raise ApiError(401, "invalid_token", "installations must be a list of ids")
    login, sub = str(claims.get("login", "")), str(claims["sub"])
    user = User(
        github_id=sub,
        login=login,
        installations=frozenset(installations),
        # Admin is the server's decision (API_ADMIN_LOGINS, or the desktop's
        # LOCAL_MODE, where the one dashboard is the owner's), never a claim.
        is_admin=settings.local_mode
        or settings.admins.allows(login, int(sub) if sub.isdigit() else None),
        demo=settings.demo_mode,
    )
    noted = sub in _NOTED_USERS or len(_NOTED_USERS) >= _MAX_NOTED
    if not user.is_admin and not user.demo and not noted:
        _NOTED_USERS.add(sub)
        logger.warning(
            "dashboard user %s (not in API_ADMIN_LOGINS) sees every repo of their"
            " installations %s, not only the repos they can open on GitHub (D137)",
            login,
            sorted(installations),
        )
    return user


def require_writable(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    """Guard for mutating endpoints: a demo instance is read-only (§15)."""
    if settings.demo_mode:
        raise ApiError(403, "demo_mode", "this instance is read-only")
