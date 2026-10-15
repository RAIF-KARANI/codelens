"""What a machine needs to run CodeLens, checked one thing at a time (D125).

Each check returns a `Check`: ok, warn (works, but worse or incomplete), or
fail (CodeLens won't work until it's fixed), plus the fix in one line. The
checks only look; nothing is installed, started or written.

Commands and HTTP go through small injectable callables (`Runner`, `Fetcher`),
so the tests can play any machine without touching this one.
"""

import platform
import shutil
import socket
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx

from core.config import Settings

# What the evaluation ran on (evals/RESULTS.md): qwen3:8b fits 8 GB of VRAM at
# an 8k window. Less memory still works, but spills to the CPU and slows down.
# "8 GB" cards report a little under 8192 MiB (an RTX 4060 Laptop: 8188).
MIN_VRAM_MB = 7680
# `env_file` with `required: false` in docker-compose.yml needs Compose 2.24.
MIN_COMPOSE = (2, 24)
# The ports docker-compose.yml publishes by default: Postgres, Redis, the API.
PORTS = {5433: ("postgres", "Postgres"), 6379: ("redis", "Redis"), 8000: ("api", "the API")}


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: Status
    detail: str
    fix: str | None = None
    # `codelens setup` takes care of it: the report then says so once,
    # instead of listing each manual fix (seen in the fresh-clone rehearsal).
    by_setup: bool = False


@dataclass(frozen=True, slots=True)
class Ran:
    """What a command did: its exit code and output, or that it isn't installed."""

    code: int
    out: str = ""
    missing: bool = False


Runner = Callable[[Sequence[str]], Ran]
Fetcher = Callable[[str], httpx.Response]


def run_command(cmd: Sequence[str]) -> Ran:
    exe = shutil.which(cmd[0])
    if exe is None:
        return Ran(127, missing=True)
    try:
        done = subprocess.run(
            [exe, *cmd[1:]], capture_output=True, text=True, timeout=20, check=False
        )
    except subprocess.TimeoutExpired:
        return Ran(124, "timed out")
    return Ran(done.returncode, (done.stdout or done.stderr).strip())


def fetch(url: str) -> httpx.Response:
    return httpx.get(url, timeout=3.0)


# --- tools


def check_git(run: Runner) -> Check:
    ran = run(["git", "--version"])
    if ran.missing:
        return Check(
            "git", Status.FAIL, "not installed", "install Git: https://git-scm.com/downloads"
        )
    return Check("git", Status.OK, ran.out.removeprefix("git version ").strip())


def check_docker(run: Runner) -> list[Check]:
    ran = run(["docker", "info", "--format", "{{.ServerVersion}}"])
    if ran.missing:
        return [
            Check(
                "Docker",
                Status.FAIL,
                "not installed",
                "install Docker Desktop: https://docs.docker.com/get-docker/",
            )
        ]
    if ran.code != 0:
        return [Check("Docker", Status.FAIL, "installed, but not running", "start Docker Desktop")]
    checks = [Check("Docker", Status.OK, f"engine {ran.out}")]
    compose = run(["docker", "compose", "version", "--short"])
    version = _version(compose.out) if compose.code == 0 else None
    if version is None:
        checks.append(
            Check(
                "Docker Compose",
                Status.FAIL,
                "not found",
                "update Docker Desktop (it ships Compose v2)",
            )
        )
    elif version < MIN_COMPOSE:
        checks.append(
            Check(
                "Docker Compose",
                Status.FAIL,
                f"{compose.out}, needs 2.24 or newer",
                "update Docker Desktop",
            )
        )
    else:
        checks.append(Check("Docker Compose", Status.OK, compose.out.lstrip("v")))
    return checks


def check_relay(smee_url: str | None, ours: set[str]) -> Check:
    """GitHub's webhooks reach this machine through a smee.io channel, relayed by
    the `smee` Compose service (`--profile relay`)."""
    if not smee_url:
        return Check(
            "webhook relay",
            Status.WARN,
            "no SMEE_URL in .env: GitHub's events can't reach this machine",
            "codelens setup (it creates a channel), or run your own tunnel",
            by_setup=True,
        )
    if "smee" in ours:
        return Check("webhook relay", Status.OK, f"relaying {smee_url}")
    return Check(
        "webhook relay",
        Status.WARN,
        f"{smee_url} set, but the relay isn't running",
        "docker compose --profile relay up -d",
    )


# --- models and hardware


def check_ollama(settings: Settings, get: Fetcher) -> list[Check]:
    base = settings.ollama_base_url.rstrip("/")
    try:
        response = get(f"{base}/api/tags")
        response.raise_for_status()
        pulled = {m["name"] for m in response.json().get("models", [])}
    except (httpx.HTTPError, ValueError, KeyError):
        return [
            Check(
                "Ollama",
                Status.FAIL,
                f"not reachable at {base}",
                "install Ollama (https://ollama.com) and start it",
            )
        ]
    checks = [Check("Ollama", Status.OK, f"running at {base}")]
    for model in _ollama_models(settings):
        if model in pulled or f"{model}:latest" in pulled:
            checks.append(Check(f"model {model}", Status.OK, "pulled"))
        else:
            checks.append(
                Check(f"model {model}", Status.FAIL, "not pulled", f"ollama pull {model}")
            )
    return checks


def _ollama_models(settings: Settings) -> list[str]:
    models = []
    if settings.llm_provider == "ollama":
        models.append(settings.llm_model)
    if settings.embed_provider == "ollama":
        models.append(settings.embed_model)
    return models


def check_gpu(run: Runner, *, system: str | None = None, machine: str | None = None) -> Check:
    system = system or platform.system()
    machine = machine or platform.machine()
    ran = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if not ran.missing and ran.code == 0 and ran.out:
        name, _, mb = ran.out.splitlines()[0].rpartition(",")
        vram = int(float(mb.strip()))
        detail = f"{name.strip()}, {vram / 1024:.0f} GB"
        if vram >= MIN_VRAM_MB:
            return Check("GPU", Status.OK, detail)
        return Check(
            "GPU",
            Status.WARN,
            f"{detail}: under 8 GB, so qwen3:8b partly runs on the CPU (slower reviews)",
        )
    if system == "Darwin" and machine == "arm64":
        return Check("GPU", Status.OK, "Apple silicon (Ollama uses Metal)")
    return Check(
        "GPU",
        Status.WARN,
        "no NVIDIA GPU found: reviews run on the CPU, a few minutes each",
    )


# --- configuration


def check_env_file(path: Path) -> Check:
    if path.exists():
        return Check(".env", Status.OK, str(path))
    return Check(".env", Status.FAIL, "missing", "cp .env.example .env", by_setup=True)


def check_github_settings(settings: Settings) -> list[Check]:
    checks = []
    secret = settings.github_webhook_secret.get_secret_value()
    if settings.github_app_id is None or not secret:
        checks.append(
            Check(
                "GitHub App settings",
                Status.FAIL,
                "GITHUB_APP_ID / GITHUB_WEBHOOK_SECRET not set",
                "create your App: uv run python -m core.github register (docs/github-app.md §2), "
                "then copy secrets/github-app.env into .env",
                by_setup=True,
            )
        )
    elif not settings.github_app_private_key_path.exists():
        checks.append(
            Check(
                "GitHub App settings",
                Status.FAIL,
                f"private key not found at {settings.github_app_private_key_path}",
                "run from the repo root, or fix GITHUB_APP_PRIVATE_KEY_PATH",
            )
        )
    elif settings.github_app_private_key_path.is_absolute():
        # The workers read it in their containers, where only the repo's
        # secrets/ is mounted (/app/secrets).
        checks.append(
            Check(
                "GitHub App settings",
                Status.FAIL,
                f"key path {settings.github_app_private_key_path} is absolute: "
                "the workers' containers can't read it",
                "set GITHUB_APP_PRIVATE_KEY_PATH=secrets/github-app.pem (setup repairs it)",
                by_setup=True,
            )
        )
    else:
        checks.append(Check("GitHub App settings", Status.OK, f"App id {settings.github_app_id}"))
    if settings.allowed_accounts:
        checks.append(
            Check("allowed accounts", Status.OK, ", ".join(sorted(settings.allowed_accounts)))
        )
    else:
        checks.append(
            Check(
                "allowed accounts",
                Status.FAIL,
                "GITHUB_ALLOWED_ACCOUNTS is empty, so no installation is acted on (D119)",
                "add your GitHub login to GITHUB_ALLOWED_ACCOUNTS in .env",
                by_setup=True,
            )
        )
    if not settings.api_jwt_secret.get_secret_value():
        checks.append(
            Check(
                "API_JWT_SECRET",
                Status.WARN,
                "not set: reviews work, but the dashboard can't call the API",
                "set it to a long random value, e.g. the output of "
                '`python -c "import secrets; print(secrets.token_urlsafe(48))"`',
                by_setup=True,
            )
        )
    return checks


# --- the running stack


def running_services(run: Runner) -> set[str]:
    """This project's Compose services that are up (run from the repo root)."""
    ran = run(["docker", "compose", "ps", "--services", "--status", "running"])
    return set(ran.out.split()) if ran.code == 0 and not ran.missing else set()


def check_ports(ours: set[str], port_free: Callable[[int], bool]) -> list[Check]:
    """The published ports are free, or held by our own containers."""
    busy = [
        f"{port} ({what})"
        for port, (service, what) in PORTS.items()
        if service not in ours and not port_free(port)
    ]
    if not busy:
        return [Check("ports", Status.OK, "5433, 6379 and 8000 free or used by CodeLens")]
    return [
        Check(
            "ports",
            Status.WARN,
            f"in use by something else: {', '.join(busy)}",
            "stop it, or change POSTGRES_PORT / REDIS_PORT / API_PORT in .env",
        )
    ]


def check_stack(api_url: str, get: Fetcher) -> Check:
    try:
        response = get(f"{api_url.rstrip('/')}/health")
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return Check(
            "CodeLens stack",
            Status.WARN,
            f"not running at {api_url}",
            "docker compose up --build -d",
            by_setup=True,
        )
    status = body.get("status")
    if status == "ok":
        return Check("CodeLens stack", Status.OK, f"healthy at {api_url}")
    failing = [name for name, c in (body.get("checks") or {}).items() if c and not c.get("ok")]
    return Check(
        "CodeLens stack",
        Status.WARN if status == "degraded" else Status.FAIL,
        f"{status}: {', '.join(failing) or 'see /health'}",
        "docker compose ps, then docker compose logs api",
    )


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _version(text: str) -> tuple[int, int] | None:
    parts = text.strip().lstrip("v").split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None
