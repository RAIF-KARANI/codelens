"""`codelens setup`: from a fresh clone to an App that reviews pull requests (D126).

Each step looks before it acts, so running setup again only does what is
still missing, and a step that needs the user (the browser, a download)
says so first:

1. `.env` from `.env.example`, with a generated `API_JWT_SECRET`.
2. The webhook relay: a smee.io channel in `SMEE_URL`. An existing App's
   channel is reused (read from GitHub), never replaced.
3. The GitHub App, by the manifest flow in the browser; its settings are
   merged into `.env`, and its owner is added to `GITHUB_ALLOWED_ACCOUNTS`.
4. The models, pulled with Ollama after asking (they are ~6 GB).
5. The stack, with the relay as a Compose service (`--profile relay`).
6. `/health`, then the install link.

Everything with side effects goes through `Tools`, so the tests replay a
fresh machine, a re-run and the failures without touching this one.
"""

import os
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from core.config import Settings
from core.github.manifest import AppCredentials
from core.privatefile import write_private
from core.setup.checks import Fetcher, Runner
from core.setup.envfile import read_env, set_env

SMEE_PREFIX = "https://smee.io/"
HEALTH_TIMEOUT_S = 240
# What the App's env file (secrets/github-app.env) carries into .env.
APP_KEYS = (
    "GITHUB_APP_ID",
    "GITHUB_APP_PRIVATE_KEY_PATH",
    "GITHUB_WEBHOOK_SECRET",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
)


@dataclass(frozen=True, slots=True)
class AppInfo:
    """An existing App, as GitHub describes it."""

    slug: str
    html_url: str
    owner: str
    webhook_url: str | None


@dataclass
class Tools:
    run: Runner  # a quick command, output captured
    stream: Callable[[Sequence[str]], int]  # a long command, output shown live
    get: Fetcher
    ask: Callable[[str, str | None], str]  # a question, with an optional default
    confirm: Callable[[str], bool]
    new_smee_channel: Callable[[], str]
    create_app: Callable[[str, str], tuple[AppCredentials, Path, Path]]  # name, webhook URL
    app_info: Callable[[Settings], AppInfo]
    say: Callable[[str], None] = print
    sleep: Callable[[float], None] = time.sleep
    settings_for: Callable[[Path], Settings] = field(
        default=lambda env: Settings(_env_file=env)  # type: ignore[call-arg]
    )


class SetupStopped(Exception):
    """A step can't go on without the user; the message says what to do."""


def setup(root: Path, tools: Tools, *, api_url: str = "http://localhost:8000") -> str:
    """Bring the machine up; returns the App's install link."""
    env = root / ".env"
    _env_file(root, env, tools)
    info, install = _github_app(root, env, tools)
    _models(tools.settings_for(env), tools)
    _start(env, tools)
    _wait_healthy(api_url, tools)
    link = install or f"{info.html_url}/installations/new"  # type: ignore[union-attr]
    tools.say(f"\nCodeLens is running. Install your App on a repository:\n  {link}")
    return link


# --- 1. .env


def _env_file(root: Path, env: Path, tools: Tools) -> None:
    if not env.exists():
        example = root / ".env.example"
        if not example.exists():
            raise SetupStopped("no .env.example here: run setup from the repo root")
        # Owner-only from the start: it is about to hold secrets (D132).
        write_private(env, example.read_text(encoding="utf-8"))
        tools.say("created .env from .env.example")
    else:
        # A .env from before D132 may be readable by everyone: narrow it.
        os.chmod(env, 0o600)
    if not read_env(env).get("API_JWT_SECRET"):
        # Signs the dashboard's API tokens; the API wants at least 32 bytes.
        set_env(env, {"API_JWT_SECRET": secrets.token_urlsafe(48)})
        tools.say("generated API_JWT_SECRET")


# --- 2 and 3. the relay and the App


def _github_app(root: Path, env: Path, tools: Tools) -> tuple[AppInfo | None, str | None]:
    values = read_env(env)
    if values.get("GITHUB_APP_ID"):
        info = tools.app_info(tools.settings_for(env))
        tools.say(f"GitHub App: {info.slug} (already set up)")
        if not values.get("SMEE_URL"):
            if info.webhook_url and info.webhook_url.startswith(SMEE_PREFIX):
                set_env(env, {"SMEE_URL": info.webhook_url})
                tools.say(f"webhook relay: using the App's channel {info.webhook_url}")
            else:
                tools.say(
                    f"webhook relay: the App sends webhooks to {info.webhook_url or 'nowhere'}, "
                    "not smee.io; keep your own tunnel running"
                )
        _key_path_for_containers(root, env, tools)
        _allow(env, info.owner, tools)
        return info, None

    smee = values.get("SMEE_URL") or _new_channel(env, tools)
    login = tools.ask("Your GitHub username (the App is created on this account)", None).strip()
    name = tools.ask("A name for your App (unique on GitHub)", f"CodeLens ({login})").strip()
    tools.say("Creating the App: confirm it in the browser window that opens ...")
    creds, _key, app_env = tools.create_app(name, smee)
    saved = read_env(app_env)
    set_env(env, {key: saved[key] for key in APP_KEYS if key in saved})
    tools.say(f"GitHub App: created {creds.slug}; its settings are in .env")
    _key_path_for_containers(root, env, tools)
    _allow(env, creds.owner, tools)
    return None, f"{creds.html_url}/installations/new"


def _key_path_for_containers(root: Path, env: Path, tools: Tools) -> None:
    """The workers read the App's key inside their containers, where the repo's
    `secrets/` is mounted at `/app/secrets`, so `.env` must name it relative to
    the repo root. The manifest flow writes the path it was given, which is
    absolute when setup runs it: the fresh-clone rehearsal's first index jobs
    failed on `C:/Users/.../secrets/github-app.pem`. This also repairs such a
    path left by an earlier run."""
    value = read_env(env).get("GITHUB_APP_PRIVATE_KEY_PATH", "")
    path = Path(value)
    if not value or not path.is_absolute():
        return
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        tools.say(
            f"warning: the App's key is outside this checkout ({value}); the containers "
            "can't read it: move it to secrets/ and set GITHUB_APP_PRIVATE_KEY_PATH"
        )
        return
    set_env(env, {"GITHUB_APP_PRIVATE_KEY_PATH": relative})
    tools.say(f"GitHub App key: {relative} (relative, so the containers can read it)")


def _new_channel(env: Path, tools: Tools) -> str:
    url = tools.new_smee_channel()
    set_env(env, {"SMEE_URL": url})
    tools.say(f"webhook relay: created {url}")
    return url


def _allow(env: Path, owner: str, tools: Tools) -> None:
    """Make sure the App's owner is allowed (D119): an empty list means nobody."""
    current = read_env(env).get("GITHUB_ALLOWED_ACCOUNTS", "")
    accounts = [a.strip() for a in current.split(",") if a.strip()]
    if owner.lower() not in {a.lower() for a in accounts}:
        set_env(env, {"GITHUB_ALLOWED_ACCOUNTS": ",".join([*accounts, owner])})
        tools.say(f"allowed accounts: added {owner}")


# --- 4. models


def _models(settings: Settings, tools: Tools) -> None:
    wanted = [
        model
        for model, provider in (
            (settings.llm_model, settings.llm_provider),
            (settings.embed_model, settings.embed_provider),
        )
        if provider == "ollama"
    ]
    if not wanted:
        return
    base = settings.ollama_base_url.rstrip("/")
    try:
        response = tools.get(f"{base}/api/tags")
        response.raise_for_status()
        pulled = {m["name"] for m in response.json().get("models", [])}
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise SetupStopped(
            f"Ollama isn't reachable at {base}: install it (https://ollama.com), start it, "
            "and run setup again"
        ) from exc
    missing = [m for m in wanted if m not in pulled and f"{m}:latest" not in pulled]
    if not missing:
        tools.say(f"models: {', '.join(wanted)} already pulled")
        return
    if not tools.confirm(f"Pull {', '.join(missing)} with Ollama now? (about 6 GB in total)"):
        raise SetupStopped(
            f"pull them when ready: {' && '.join(f'ollama pull {m}' for m in missing)}"
        )
    for model in missing:
        if tools.stream(["ollama", "pull", model]) != 0:
            raise SetupStopped(f"`ollama pull {model}` failed; run it yourself to see why")


# --- 5 and 6. start, and wait


def _start(env: Path, tools: Tools) -> None:
    ran = tools.run(["docker", "info", "--format", "{{.ServerVersion}}"])
    if ran.missing or ran.code != 0:
        raise SetupStopped("Docker isn't running: start Docker Desktop and run setup again")
    profile = ["--profile", "relay"] if read_env(env).get("SMEE_URL") else []
    tools.say("starting CodeLens with Docker Compose (the first build takes a few minutes) ...")
    if tools.stream(["docker", "compose", *profile, "up", "--build", "-d"]) != 0:
        raise SetupStopped("`docker compose up` failed: see the output above")


def _wait_healthy(api_url: str, tools: Tools) -> None:
    deadline = HEALTH_TIMEOUT_S
    while deadline > 0:
        try:
            status = tools.get(f"{api_url}/health").json().get("status")
        except (httpx.HTTPError, ValueError):
            status = None
        if status in ("ok", "degraded"):
            tools.say(f"the API is up ({status})")
            return
        tools.sleep(3)
        deadline -= 3
    raise SetupStopped("the API didn't come up: docker compose ps, then docker compose logs api")
