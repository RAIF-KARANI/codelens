"""Run your own CodeLens (Phase 8).

    codelens doctor          # what this machine has, and what's missing
    python -m core.setup doctor

`codelens` is installed with the workspace (`uv sync`): run it as
`uv run codelens doctor` from the repo root, where `.env` lives.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from core.config import Settings, get_settings
from core.github import GitHubClient, GitHubError, app_jwt, load_private_key
from core.setup.checks import (
    Check,
    Fetcher,
    Runner,
    Status,
    check_docker,
    check_env_file,
    check_git,
    check_github_settings,
    check_gpu,
    check_node,
    check_ollama,
    check_ports,
    check_stack,
    fetch,
    port_is_free,
    run_command,
    running_services,
)

MARK = {Status.OK: "ok  ", Status.WARN: "warn", Status.FAIL: "FAIL"}


async def check_github_app(settings: Settings) -> Check:
    """The App's credentials work, and where it's installed."""
    try:
        key = load_private_key(settings.github_app_private_key_path)
        async with GitHubClient(settings.github_api_url) as gh:
            token = app_jwt(settings.github_app_id, key)  # type: ignore[arg-type]
            app = await gh.get_app(token)
            installations = await gh.list_installations(token)
    except (GitHubError, OSError, ValueError) as exc:
        return Check(
            "GitHub App",
            Status.FAIL,
            f"GitHub refused the App's credentials ({type(exc).__name__})",
            "check GITHUB_APP_ID and the private key: uv run python -m core.github check",
        )
    if not installations:
        return Check(
            "GitHub App",
            Status.WARN,
            f"{app['slug']} works, but isn't installed on any repo yet",
            f"install it: {app['html_url']}/installations/new",
        )
    accounts = sorted({i["account"]["login"] for i in installations})
    return Check(
        "GitHub App",
        Status.OK,
        f"{app['slug']}, installed for {', '.join(accounts)}",
    )


def doctor(
    settings: Settings,
    *,
    env_path: Path,
    api_url: str,
    run: Runner = run_command,
    get: Fetcher = fetch,
    port_free: Callable[[int], bool] = port_is_free,
    github_app: Callable[[Settings], Check] | None = None,
) -> list[Check]:
    checks: list[Check] = [check_git(run), *check_docker(run), check_node(run)]
    checks += [*check_ollama(settings, get), check_gpu(run)]
    checks.append(check_env_file(env_path))
    github = check_github_settings(settings)
    checks += github
    if all(c.status is not Status.FAIL for c in github if c.name == "GitHub App settings"):
        checks.append((github_app or _github_app_sync)(settings))
    checks += [*check_ports(running_services(run), port_free), check_stack(api_url, get)]
    return checks


def _github_app_sync(settings: Settings) -> Check:
    return asyncio.run(check_github_app(settings))


def report(checks: Iterable[Check]) -> int:
    checks = list(checks)
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"  [{MARK[c.status]}] {c.name.ljust(width)}  {c.detail}")
    fixes = [c for c in checks if c.fix and c.status is not Status.OK]
    if fixes:
        print("\nTo do, in order:")
        for i, c in enumerate(fixes, 1):
            print(f"  {i}. {c.name}: {c.fix}")
    failed = sum(c.status is Status.FAIL for c in checks)
    warned = sum(c.status is Status.WARN for c in checks)
    if failed:
        print(f"\n{failed} problem(s) to fix before CodeLens can review pull requests.")
    elif warned:
        print(f"\nReady, with {warned} warning(s).")
    else:
        print("\nAll set: open a pull request on an installed repo.")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="codelens", description="Run your own CodeLens.")
    sub = parser.add_subparsers(dest="command", required=True)
    doc = sub.add_parser("doctor", help="check what this machine has, and what's missing")
    doc.add_argument("--env-file", type=Path, default=Path(".env"))
    doc.add_argument("--api-url", default="http://localhost:8000")
    args = parser.parse_args()

    settings = get_settings()
    print("CodeLens doctor: checking this machine (nothing is changed)\n")
    sys.exit(report(doctor(settings, env_path=args.env_file, api_url=args.api_url)))


if __name__ == "__main__":
    main()
