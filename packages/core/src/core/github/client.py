"""A small async client for the GitHub REST API (v3): only the calls CodeLens
makes, each written out, rather than a general-purpose SDK."""

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any, Self

import httpx

# Pinning the API version means a GitHub-side change can't silently alter
# response shapes under us.
API_VERSION = "2022-11-28"


class GitHubError(Exception):
    """GitHub answered with an error status. `status` is the HTTP code."""

    def __init__(self, status: int, message: str, *, method: str, path: str) -> None:
        super().__init__(f"GitHub {method} {path}: {status} {message}")
        self.status = status


@dataclass(frozen=True, slots=True)
class InstallationToken:
    token: str
    expires_at: datetime


class GitHubClient:
    def __init__(
        self,
        base_url: str = "https://api.github.com",
        *,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,  # tests: httpx.MockTransport
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                # GitHub rejects requests without a User-Agent.
                "User-Agent": "codelens",
            },
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- as the App (authenticated with an App JWT, auth.app_jwt)

    async def get_app(self, app_jwt: str) -> dict[str, Any]:
        """The App itself: name, slug, permissions, events."""
        return await self._request("GET", "/app", token=app_jwt)

    async def list_installations(self, app_jwt: str) -> list[dict[str, Any]]:
        """Accounts that installed the App (first 100, plenty for a laptop)."""
        return await self._request("GET", "/app/installations?per_page=100", token=app_jwt)

    async def create_installation_token(
        self, installation_id: int, app_jwt: str
    ) -> InstallationToken:
        data = await self._request(
            "POST", f"/app/installations/{installation_id}/access_tokens", token=app_jwt
        )
        # GitHub sends "2026-10-12T17:00:00Z"; fromisoformat reads the Z since 3.11.
        return InstallationToken(data["token"], datetime.fromisoformat(data["expires_at"]))

    # --- as an installation (an installation token, auth.InstallationTokens)

    async def get_repo(self, full_name: str, token: str) -> dict[str, Any]:
        """A repository: default branch, visibility, ..."""
        return await self._request("GET", f"/repos/{full_name}", token=token)

    # --- unauthenticated

    async def convert_manifest(self, code: str) -> dict[str, Any]:
        """Finish the manifest flow: trade the one-time code GitHub redirected
        with for the new App's id, private key and webhook secret."""
        return await self._request("POST", f"/app-manifests/{code}/conversions", token=None)

    async def _request(self, method: str, path: str, *, token: str | None) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = await self._http.request(method, path, headers=headers)
        if response.is_error:
            raise GitHubError(response.status_code, _message(response), method=method, path=path)
        return response.json() if response.content else None


def _message(response: httpx.Response) -> str:
    # GitHub's error bodies are {"message": "...", "documentation_url": "..."}.
    try:
        return str(response.json().get("message", response.reason_phrase))
    except ValueError:
        return response.reason_phrase
