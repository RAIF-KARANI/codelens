"""Run your own CodeLens (Phase 8).

    codelens doctor          # what this machine has, and what's missing
    codelens setup           # set it up: .env, the relay, your App, models, the stack
    python -m core.setup doctor | setup

`codelens` is installed with the workspace (`uv sync`): run it as
`uv run codelens ...` from the repo root, where `.env` lives.
"""

import argparse
import asyncio
import json
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

import httpx

from core.config import Settings, get_settings
from core.github import GitHubClient, GitHubError, app_jwt, load_private_key
from core.github.__main__ import RegistrationFailed, create_app
from core.github.manifest import AppCredentials
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
    check_ollama,
    check_ports,
    check_relay,
    check_stack,
    fetch,
    port_is_free,
    run_command,
    running_services,
)
from core.setup.envfile import read_env
from core.setup.setup import SMEE_PREFIX, AppInfo, SetupStopped, Tools, setup

MARK = {Status.OK: "ok  ", Status.WARN: "warn", Status.FAIL: "FAIL"}


# --- doctor


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
    checks: list[Check] = [check_git(run), *check_docker(run)]
    checks += [*check_ollama(settings, get), check_gpu(run)]
    checks.append(check_env_file(env_path))
    github = check_github_settings(settings)
    checks += github
    if all(c.status is not Status.FAIL for c in github if c.name == "GitHub App settings"):
        checks.append((github_app or _github_app_sync)(settings))
    ours = running_services(run)
    checks.append(check_relay(read_env(env_path).get("SMEE_URL"), ours))
    checks += [*check_ports(ours, port_free), check_stack(api_url, get)]
    return checks


def desktop_doctor(
    settings: Settings,
    *,
    get: Fetcher = fetch,
    github_app: Callable[[Settings], Check] | None = None,
) -> list[Check]:
    """What CodeLens Desktop's wizard asks the stack (Phase 11 step 5b, D174),
    run inside its API container: Ollama and the models, the App's settings
    and the App on GitHub. The rest of `doctor` is the app's own business
    there (Windows, Docker, the GPU, ports) or doesn't apply (git, the relay,
    .env), so it isn't asked."""
    checks = check_ollama(settings, get)
    github = check_github_settings(settings)
    checks += github
    if all(c.status is not Status.FAIL for c in github if c.name == "GitHub App settings"):
        checks.append((github_app or _github_app_sync)(settings))
    return checks


def as_json(checks: Iterable[Check]) -> str:
    """The checks as JSON: [{name, status, detail, fix}], for a program to show."""
    return json.dumps(
        [
            {"name": c.name, "status": str(c.status), "detail": c.detail, "fix": c.fix}
            for c in checks
        ]
    )


def _github_app_sync(settings: Settings) -> Check:
    return asyncio.run(check_github_app(settings))


def report(checks: Iterable[Check]) -> int:
    checks = list(checks)
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"  [{MARK[c.status]}] {c.name.ljust(width)}  {c.detail}")
    open_ = [c for c in checks if c.fix and c.status is not Status.OK]
    by_setup = [c for c in open_ if c.by_setup]
    todo = [f"{c.name}: {c.fix}" for c in open_ if not c.by_setup]
    if by_setup:
        names = ", ".join(c.name for c in by_setup)
        # Last: setup needs the tools above (Docker, Ollama) to do its part.
        todo.append(f"uv run codelens setup  (it takes care of: {names})")
    if todo:
        print("\nTo do, in order:")
        for i, line in enumerate(todo, 1):
            print(f"  {i}. {line}")
    failed = sum(c.status is Status.FAIL for c in checks)
    warned = sum(c.status is Status.WARN for c in checks)
    if failed:
        print(f"\n{failed} problem(s) to fix before CodeLens can review pull requests.")
    elif warned:
        print(f"\nReady, with {warned} warning(s).")
    else:
        print("\nAll set: open a pull request on an installed repo.")
    return 1 if failed else 0


# --- setup: the real tools


def stream_command(cmd: Sequence[str]) -> int:
    """A long command (a model download, a Docker build) with its output shown."""
    try:
        return subprocess.run(list(cmd), check=False).returncode
    except FileNotFoundError:
        return 127


def ask(question: str, default: str | None) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        answer = input(f"{question}{suffix}: ").strip()
        if answer or default:
            return answer or default  # type: ignore[return-value]


def new_smee_channel() -> str:
    """smee.io makes a channel on GET /new and redirects to it."""
    response = httpx.get(f"{SMEE_PREFIX}new", follow_redirects=False, timeout=20)
    url = response.headers.get("location", "")
    if not url.startswith(SMEE_PREFIX):
        raise SetupStopped("smee.io didn't create a channel; try again, or set SMEE_URL yourself")
    return url


def app_info(settings: Settings) -> AppInfo:
    async def go() -> AppInfo:
        key = load_private_key(settings.github_app_private_key_path)
        async with GitHubClient(settings.github_api_url) as gh:
            token = app_jwt(settings.github_app_id, key)  # type: ignore[arg-type]
            app = await gh.get_app(token)
            hook = await gh.get_app_hook_config(token)
        owner = app["owner"]
        return AppInfo(
            app["slug"], app["html_url"], owner["login"], hook.get("url"), owner.get("id")
        )

    try:
        return asyncio.run(go())
    except (GitHubError, OSError, ValueError) as exc:
        raise SetupStopped(
            f"GitHub refused the App in .env ({type(exc).__name__}): "
            "check it with uv run python -m core.github check"
        ) from exc


def real_tools(root: Path, *, yes: bool) -> Tools:
    def confirm(question: str) -> bool:
        return yes or input(f"{question} [y/N]: ").strip().lower() in ("y", "yes")

    def make_app(name: str, webhook_url: str) -> tuple[AppCredentials, Path, Path]:
        try:
            return create_app(
                Settings(_env_file=root / ".env"),  # type: ignore[call-arg]
                name=name,
                webhook_url=webhook_url,
                secrets_dir=root / "secrets",
            )
        except RegistrationFailed as exc:
            raise SetupStopped(f"the App wasn't created: {exc}") from exc

    return Tools(
        run=run_command,
        stream=stream_command,
        get=fetch,
        ask=ask,
        confirm=confirm,
        new_smee_channel=new_smee_channel,
        create_app=make_app,
        app_info=app_info,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="codelens", description="Run your own CodeLens.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_ in (
        ("doctor", "check what this machine has, and what's missing (changes nothing)"),
        ("setup", "set CodeLens up on this machine; safe to run again"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--env-file", type=Path, default=Path(".env"))
        p.add_argument("--api-url", default="http://localhost:8000")
        if name == "doctor":
            p.add_argument(
                "--desktop", action="store_true", help="only what CodeLens Desktop asks the stack"
            )
            p.add_argument("--json", action="store_true", help="the checks as JSON, nothing else")
        if name == "setup":
            p.add_argument("--yes", action="store_true", help="pull missing models without asking")
    args = parser.parse_args()

    if args.command == "doctor":
        if args.desktop:
            checks = desktop_doctor(get_settings())
        else:
            checks = doctor(get_settings(), env_path=args.env_file, api_url=args.api_url)
        if args.json:
            print(as_json(checks))
            sys.exit(1 if any(c.status is Status.FAIL for c in checks) else 0)
        print("CodeLens doctor: checking this machine (nothing is changed)\n")
        sys.exit(report(checks))

    root = args.env_file.resolve().parent
    print("CodeLens setup: each step is skipped if it's already done\n")
    try:
        setup(root, real_tools(root, yes=args.yes), api_url=args.api_url)
    except SetupStopped as exc:
        print(f"\nSetup stopped: {exc}", file=sys.stderr)
        sys.exit(1)
    print("\nChecking the result:\n")
    settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    sys.exit(report(doctor(settings, env_path=args.env_file, api_url=args.api_url)))


if __name__ == "__main__":
    main()
