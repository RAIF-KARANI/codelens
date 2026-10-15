"""Set up and check the GitHub App (docs/github-app.md).

python -m core.github register --name "CodeLens (you)" --webhook-url https://smee.io/xyz
python -m core.github check
"""

import argparse
import asyncio
import sys
import webbrowser
from pathlib import Path

from core.config import Settings, get_settings
from core.github import GitHubClient, GitHubError, InstallationTokens, app_jwt, load_private_key
from core.github.manifest import EVENTS, PERMISSIONS, AppCredentials
from core.github.register import Registration

REGISTER_TIMEOUT_S = 15 * 60


class RegistrationFailed(Exception):
    pass


def create_app(
    settings: Settings,
    *,
    name: str,
    webhook_url: str,
    secrets_dir: Path,
    org: str | None = None,
    port: int = 8765,
) -> tuple[AppCredentials, Path, Path]:
    """Run the manifest flow in the user's browser; returns the saved credentials
    and where they were written (the key, the env file). Also used by
    `codelens setup`."""

    def convert(code: str) -> AppCredentials:
        async def go() -> AppCredentials:
            async with GitHubClient(settings.github_api_url) as gh:
                return AppCredentials.from_conversion(await gh.convert_manifest(code))

        return asyncio.run(go())

    registration = Registration(
        name=name,
        webhook_url=webhook_url,
        convert=convert,
        secrets_dir=secrets_dir,
        org=org,
        port=port,
    )
    registration.start()
    print(f"Open {registration.url}/ to create the App (opening your browser) ...", flush=True)
    webbrowser.open(f"{registration.url}/")
    finished = registration.done.wait(REGISTER_TIMEOUT_S)
    registration.stop()
    if not finished:
        raise RegistrationFailed("timed out waiting for GitHub's redirect")
    if registration.saved is None:
        raise RegistrationFailed(registration.error or "the App was not created")
    return registration.saved


def register(args: argparse.Namespace, settings: Settings) -> int:
    try:
        creds, key_path, env_path = create_app(
            settings,
            name=args.name,
            webhook_url=args.webhook_url,
            secrets_dir=args.secrets_dir,
            org=args.org,
            port=args.port,
        )
    except RegistrationFailed as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"\nCreated {creds.slug} (App id {creds.app_id}).\n"
        f"  private key  {key_path}\n"
        f"  settings     {env_path}  <- copy these lines into .env\n"
        f"\nNext: install it on a test repo: {creds.html_url}/installations/new\n"
        "then run: python -m core.github check"
    )
    return 0


async def check(settings: Settings) -> int:
    """Walk the whole auth chain: App JWT -> the App -> each installation's token."""
    if settings.github_app_id is None:
        print("error: GITHUB_APP_ID is not set (see docs/github-app.md)", file=sys.stderr)
        return 1
    key = load_private_key(settings.github_app_private_key_path)
    async with GitHubClient(settings.github_api_url) as gh:
        token = app_jwt(settings.github_app_id, key)
        app = await gh.get_app(token)
        print(f"App        {app['name']} ({app['html_url']})")
        print(f"  permissions {app.get('permissions')}  (expected {PERMISSIONS})")
        print(f"  events      {app.get('events')}  (expected {EVENTS})")
        secret = "set" if settings.github_webhook_secret.get_secret_value() else "NOT SET"
        print(f"  webhook secret in .env: {secret}")
        installations = await gh.list_installations(token)
        print(f"\n{len(installations)} installation(s)")
        tokens = InstallationTokens(gh, settings.github_app_id, key)
        for inst in installations:
            account = inst["account"]
            await tokens.get(inst["id"])  # proves the App can act for this installation
            print(
                f"  {account['login']} ({account['type']}), id {inst['id']}, "
                f"repos: {inst.get('repository_selection')}: installation token OK"
            )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m core.github", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register", help="create the GitHub App from its manifest")
    reg.add_argument("--name", required=True, help="App name; must be unique on GitHub")
    reg.add_argument("--webhook-url", required=True, help="where GitHub sends events (smee.io)")
    reg.add_argument("--org", help="create it under this organisation instead of your account")
    reg.add_argument("--port", type=int, default=8765, help="local callback port (default 8765)")
    reg.add_argument("--secrets-dir", type=Path, default=Path("secrets"))
    sub.add_parser("check", help="verify the App's credentials and installations")
    args = parser.parse_args()

    settings = get_settings()
    try:
        if args.command == "register":
            code = register(args, settings)
        else:
            code = asyncio.run(check(settings))
    except (GitHubError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
