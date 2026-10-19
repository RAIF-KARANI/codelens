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


@dataclass(frozen=True, slots=True)
class Conditional:
    """One conditional GET's answer (the poller's, D145). With the ETag of an
    earlier answer, GitHub replies 304 Not Modified, with no body, when
    nothing changed."""

    not_modified: bool
    data: Any  # the parsed body; None when not modified
    etag: str | None
    next_url: str | None  # the next page (the Link header), if any
    rate_remaining: int | None  # X-RateLimit-Remaining, to see what polling costs


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

    async def get_app_hook_config(self, app_jwt: str) -> dict[str, Any]:
        """Where the App sends its webhooks (`url`); the secret comes back masked."""
        return await self._request("GET", "/app/hook/config", token=app_jwt)

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

    async def get_repository(self, github_id: int, token: str) -> dict[str, Any]:
        """A repository by its id, which a rename or transfer never changes:
        its current `full_name`, default branch, ..."""
        return await self._request("GET", f"/repositories/{github_id}", token=token)

    async def get_pull_request(self, full_name: str, number: int, token: str) -> dict[str, Any]:
        """A pull request as it is now: its state, head and base."""
        return await self._request("GET", f"/repos/{full_name}/pulls/{number}", token=token)

    async def create_review(
        self, full_name: str, number: int, review: dict[str, Any], token: str
    ) -> dict[str, Any]:
        """Post a pull request review (body + inline comments) in one call."""
        return await self._request(
            "POST", f"/repos/{full_name}/pulls/{number}/reviews", token=token, json=review
        )

    async def get_conditional(
        self,
        path: str,
        token: str,
        *,
        etag: str | None = None,
        accept: str | None = None,
    ) -> Conditional:
        """GET `path` (or a full URL from a Link header), sending `etag` as
        If-None-Match. `accept` asks for another representation, e.g.
        `application/vnd.github.sha` for a bare commit sha as text."""
        headers = {"Authorization": f"Bearer {token}"}
        if etag:
            headers["If-None-Match"] = etag
        if accept:
            headers["Accept"] = accept
        response = await self._http.get(path, headers=headers)
        remaining = response.headers.get("X-RateLimit-Remaining")
        rate = int(remaining) if remaining and remaining.isdigit() else None
        if response.status_code == 304:
            return Conditional(True, None, etag, None, rate)
        if response.is_error:
            raise GitHubError(response.status_code, _message(response), method="GET", path=path)
        is_json = "json" in response.headers.get("Content-Type", "")
        return Conditional(
            False,
            response.json() if is_json else response.text.strip(),
            response.headers.get("ETag"),
            response.links.get("next", {}).get("url"),
            rate,
        )

    # --- unauthenticated

    async def convert_manifest(self, code: str) -> dict[str, Any]:
        """Finish the manifest flow: trade the one-time code GitHub redirected
        with for the new App's id, private key and webhook secret."""
        return await self._request("POST", f"/app-manifests/{code}/conversions", token=None)

    async def _request(self, method: str, path: str, *, token: str | None, json: Any = None) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = await self._http.request(method, path, headers=headers, json=json)
        if response.is_error:
            raise GitHubError(response.status_code, _message(response), method=method, path=path)
        return response.json() if response.content else None


def _message(response: httpx.Response) -> str:
    # GitHub's error bodies are {"message": "...", "documentation_url": "..."}.
    try:
        return str(response.json().get("message", response.reason_phrase))
    except ValueError:
        return response.reason_phrase
