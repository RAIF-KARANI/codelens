"""Who is calling. The dashboard's Next.js server sends
`Authorization: Bearer <JWT>`: HS256, signed with the shared API_JWT_SECRET,
5-minute expiry, `sub` = the GitHub user id, `login` = their handle
(planning.md §7).

Until GitHub installations exist (Phase 5 step 3), any valid token can see
every repo; the per-repo access check arrives with them.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.dependencies import get_settings
from api.errors import ApiError
from core.config import Settings

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(minutes=5)

# auto_error=False: a missing header becomes our own 401 shape, not FastAPI's 403.
_bearer = HTTPBearer(auto_error=False)
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True, slots=True)
class User:
    github_id: str
    login: str


def issue_token(secret: str, github_id: str, login: str, *, ttl: timedelta = TOKEN_TTL) -> str:
    now = datetime.now(UTC)
    claims = {"sub": github_id, "login": login, "iat": now, "exp": now + ttl}
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
    return User(github_id=str(claims["sub"]), login=str(claims.get("login", "")))


def require_writable(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    """Guard for mutating endpoints: a demo instance is read-only (§15)."""
    if settings.demo_mode:
        raise ApiError(403, "demo_mode", "this instance is read-only")
