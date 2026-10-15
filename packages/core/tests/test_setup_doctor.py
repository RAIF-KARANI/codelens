"""codelens doctor on imaginary machines: every command and HTTP call is faked."""

from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from core.config import Settings
from core.setup.__main__ import doctor, report
from core.setup.checks import (
    Check,
    Ran,
    Status,
    check_docker,
    check_github_settings,
    check_gpu,
    check_ollama,
    check_ports,
    check_stack,
    running_services,
)


def machine(**answers: Ran) -> "callable":
    """A Runner answering by the command's first word(s); unknown tools are missing."""

    def run(cmd: Sequence[str]) -> Ran:
        for key in (" ".join(cmd[:3]), " ".join(cmd[:2]), cmd[0]):
            if key in answers:
                return answers[key]
        return Ran(127, missing=True)

    return run


def http(routes: dict[str, object]) -> "callable":
    """A Fetcher: a URL suffix → JSON body (or an exception class to raise)."""

    def get(url: str) -> httpx.Response:
        for suffix, body in routes.items():
            if url.endswith(suffix):
                if isinstance(body, type) and issubclass(body, Exception):
                    raise body("down")
                return httpx.Response(200, json=body, request=httpx.Request("GET", url))
        raise httpx.ConnectError("refused")

    return get


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {c.name: c for c in checks}


# --- one check at a time


def test_docker_missing_stopped_old_and_fine() -> None:
    assert check_docker(machine())[0].status is Status.FAIL
    stopped = check_docker(machine(docker=Ran(1, "Cannot connect to the Docker daemon")))
    assert (stopped[0].status, stopped[0].fix) == (Status.FAIL, "start Docker Desktop")

    old = check_docker(
        machine(**{"docker info": Ran(0, "27.0"), "docker compose": Ran(0, "v2.20.1")})
    )
    assert old[1].status is Status.FAIL and "2.24" in old[1].detail

    fine = check_docker(
        machine(**{"docker info": Ran(0, "29.8.0"), "docker compose": Ran(0, "2.40.0")})
    )
    assert [c.status for c in fine] == [Status.OK, Status.OK]


def test_ollama_down_or_missing_a_model() -> None:
    s = settings(llm_model="qwen3:8b", embed_model="qwen3-embedding:0.6b")
    down = check_ollama(s, http({}))
    assert (down[0].status, len(down)) == (Status.FAIL, 1)

    one = by_name(check_ollama(s, http({"/api/tags": {"models": [{"name": "qwen3:8b"}]}})))
    assert one["model qwen3:8b"].status is Status.OK
    assert one["model qwen3-embedding:0.6b"].fix == "ollama pull qwen3-embedding:0.6b"


def test_an_untagged_model_is_found_as_latest() -> None:
    s = settings(llm_model="qwen3", embed_provider="fake")
    checks = check_ollama(s, http({"/api/tags": {"models": [{"name": "qwen3:latest"}]}}))
    assert [c.status for c in checks] == [Status.OK, Status.OK]


@pytest.mark.parametrize(
    ("smi", "system", "machine_", "status"),
    [
        (Ran(0, "NVIDIA GeForce RTX 4060 Laptop GPU, 8188"), "Windows", "AMD64", Status.OK),
        (Ran(0, "NVIDIA GeForce GTX 1650, 4096"), "Windows", "AMD64", Status.WARN),
        (None, "Darwin", "arm64", Status.OK),
        (None, "Linux", "x86_64", Status.WARN),
    ],
    ids=["8gb", "4gb", "apple-silicon", "cpu-only"],
)
def test_gpu(smi: Ran | None, system: str, machine_: str, status: Status) -> None:
    run = machine(**({"nvidia-smi": smi} if smi else {}))
    assert check_gpu(run, system=system, machine=machine_).status is status


def test_github_settings_fail_closed() -> None:
    empty = by_name(check_github_settings(settings()))
    assert empty["GitHub App settings"].status is Status.FAIL
    # An empty allowlist means nobody gets reviewed (D119): a failure, not a warning.
    assert empty["allowed accounts"].status is Status.FAIL
    assert empty["API_JWT_SECRET"].status is Status.WARN


def test_github_settings_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A real setup names the key relative to the repo root (the containers mount secrets/).
    monkeypatch.chdir(tmp_path)
    key = Path("app.pem")
    key.write_text("key")
    s = settings(
        github_app_id=5,
        github_webhook_secret="w",
        github_app_private_key_path=key,
        github_allowed_accounts="Me",
        api_jwt_secret="x" * 32,
    )
    assert [c.status for c in check_github_settings(s)] == [Status.OK, Status.OK]


def test_ports_held_by_our_own_containers_are_fine() -> None:
    # Postgres and Redis up, the API stopped: their ports are ours (seen on the
    # dev machine), 8000 is free.
    [ok] = check_ports({"postgres", "redis"}, lambda port: port == 8000)
    assert ok.status is Status.OK
    # Something else on 8000 while our API is down is worth a warning.
    [busy] = check_ports({"postgres", "redis"}, lambda port: False)
    assert busy.status is Status.WARN and busy.detail.endswith("8000 (the API)")


def test_running_services_asks_compose() -> None:
    run = machine(**{"docker compose ps": Ran(0, "postgres\nredis\n")})
    assert running_services(run) == {"postgres", "redis"}
    assert running_services(machine()) == set()  # no Docker: nothing of ours runs


def test_stack_states() -> None:
    url = "http://localhost:8000"
    assert check_stack(url, http({})).fix == "docker compose up --build -d"
    degraded = check_stack(
        url,
        http(
            {
                "/health": {
                    "status": "degraded",
                    "checks": {"database": {"ok": True}, "ollama": {"ok": False}},
                }
            }
        ),
    )
    assert (degraded.status, degraded.detail) == (Status.WARN, "degraded: ollama")
    assert check_stack(url, http({"/health": {"status": "ok", "checks": {}}})).status is Status.OK


# --- the whole report


def test_a_bare_machine_gets_an_ordered_to_do_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checks = doctor(
        settings(),
        env_path=tmp_path / ".env",
        api_url="http://localhost:8000",
        run=machine(git=Ran(0, "git version 2.50.0")),
        get=http({}),
        port_free=lambda port: True,
    )
    code = report(checks)
    out = capsys.readouterr().out

    assert code == 1
    statuses = by_name(checks)
    assert statuses["git"].status is Status.OK
    assert statuses["Docker"].status is Status.FAIL
    assert "GitHub App" not in statuses  # not tried without settings
    assert "To do, in order:" in out and "1. Docker: install Docker Desktop" in out
    # What setup fixes comes once, last: it needs Docker and Ollama first.
    last = out.split("To do, in order:")[1].strip().splitlines()[-3]
    assert "uv run codelens setup  (it takes care of: .env, GitHub App settings" in last


def test_a_ready_machine_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    key = Path("app.pem")
    key.write_text("key")
    env = tmp_path / ".env"
    env.write_text("SMEE_URL=https://smee.io/abc\n")
    s = settings(
        github_app_id=5,
        github_webhook_secret="w",
        github_app_private_key_path=key,
        github_allowed_accounts="me",
        api_jwt_secret="x" * 32,
        embed_provider="fake",
    )
    checks = doctor(
        s,
        env_path=env,
        api_url="http://localhost:8000",
        run=machine(
            git=Ran(0, "git version 2.50.0"),
            **{"docker info": Ran(0, "29.8.0"), "docker compose": Ran(0, "2.40.0")},
            **{"docker compose ps": Ran(0, "postgres\nredis\napi\nsmee\n")},
            **{"nvidia-smi": Ran(0, "NVIDIA RTX 4060, 8188")},
        ),
        get=http(
            {
                "/api/tags": {"models": [{"name": "qwen3:8b"}]},
                "/health": {"status": "ok", "checks": {}},
            }
        ),
        port_free=lambda port: False,  # our own stack holds them
        github_app=lambda _: Check("GitHub App", Status.OK, "codelens-me, installed for me"),
    )

    assert report(checks) == 0
    assert all(c.status is Status.OK for c in checks), [
        c for c in checks if c.status is not Status.OK
    ]
    assert "All set" in capsys.readouterr().out


def test_relay_states() -> None:
    from core.setup.checks import check_relay

    assert check_relay(None, set()).fix.startswith("codelens setup")
    assert check_relay("https://smee.io/abc", {"smee"}).status is Status.OK
    stopped = check_relay("https://smee.io/abc", {"api"})
    assert (stopped.status, stopped.fix) == (Status.WARN, "docker compose --profile relay up -d")


def test_an_absolute_key_path_fails_because_containers_cant_read_it(tmp_path: Path) -> None:
    key = tmp_path / "app.pem"
    key.write_text("key")
    s = settings(
        github_app_id=5, github_webhook_secret="w", github_app_private_key_path=key.resolve()
    )
    [app] = [c for c in check_github_settings(s) if c.name == "GitHub App settings"]
    assert app.status is Status.FAIL and app.by_setup
