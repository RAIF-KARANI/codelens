"""Creating the GitHub App from a manifest (D93).

The App's permissions and events are defined here, in code, so they are
reviewed like everything else. GitHub's manifest flow then creates the App in
one click and hands back its credentials:

1. A browser POSTs the manifest to github.com/settings/apps/new.
2. The user names and confirms the App; GitHub redirects to `redirect_url`
   with a one-time `code`.
3. We trade the code for the App's id, private key and webhook secret
   (`GitHubClient.convert_manifest`) and write them to secrets/.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.privatefile import private_dir, write_private

# Least privilege (idea.md §9.1): read code, write only pull request reviews.
PERMISSIONS = {"contents": "read", "metadata": "read", "pull_requests": "write"}
# `installation` and `installation_repositories` events are always delivered
# to an App; these are the ones it has to ask for.
EVENTS = ["pull_request", "push"]
HOMEPAGE = "https://github.com/RAIF-KARANI/codelens"
# Where GitHub sends users back after the dashboard's "Sign in with GitHub"
# (apps/web, D113). More can be added in the App's settings, e.g. for a deploy.
DASHBOARD_CALLBACK_URL = "http://localhost:3000/api/auth/callback"


def build_manifest(*, name: str, webhook_url: str, redirect_url: str) -> dict[str, Any]:
    return {
        "name": name,
        "url": HOMEPAGE,
        "hook_attributes": {"url": webhook_url, "active": True},
        "redirect_url": redirect_url,
        "callback_urls": [DASHBOARD_CALLBACK_URL],
        "public": False,  # only its owner can install it
        "default_permissions": PERMISSIONS,
        "default_events": EVENTS,
    }


def new_app_url(org: str | None) -> str:
    """Where the manifest is POSTed: a personal account, or an organisation."""
    if org:
        return f"https://github.com/organizations/{org}/settings/apps/new"
    return "https://github.com/settings/apps/new"


@dataclass(frozen=True, slots=True)
class AppCredentials:
    app_id: int
    slug: str
    html_url: str
    pem: str
    webhook_secret: str
    client_id: str
    client_secret: str
    owner: str  # the account that created the App: the first one it works for

    @classmethod
    def from_conversion(cls, data: dict[str, Any]) -> "AppCredentials":
        return cls(
            app_id=int(data["id"]),
            slug=data["slug"],
            html_url=data["html_url"],
            pem=data["pem"],
            webhook_secret=data["webhook_secret"],
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            owner=data["owner"]["login"],
        )


def save_credentials(creds: AppCredentials, secrets_dir: Path) -> tuple[Path, Path]:
    """Write the private key and an env file with the other settings.

    Returns (key path, env path). Nothing secret is printed: it goes to files
    in a gitignored directory, readable only by the owner where the OS
    supports it, from the moment each file exists (D132).
    """
    private_dir(secrets_dir)
    key_path = secrets_dir / "github-app.pem"
    env_path = secrets_dir / "github-app.env"
    write_private(key_path, creds.pem)
    write_private(
        env_path,
        "# Created by `python -m core.github register`. Copy these lines into .env.\n"
        f"GITHUB_APP_ID={creds.app_id}\n"
        f"GITHUB_APP_PRIVATE_KEY_PATH={key_path.as_posix()}\n"
        f"GITHUB_WEBHOOK_SECRET={creds.webhook_secret}\n"
        "# Whose installations are acted on (comma-separated); unset means nobody.\n"
        f"GITHUB_ALLOWED_ACCOUNTS={creds.owner}\n"
        "# For GitHub login on the dashboard (Phase 6); unused until then.\n"
        f"GITHUB_CLIENT_ID={creds.client_id}\n"
        f"GITHUB_CLIENT_SECRET={creds.client_secret}\n",
    )
    return key_path, env_path
