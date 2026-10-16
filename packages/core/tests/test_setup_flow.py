"""codelens setup on imaginary machines: a fresh clone, a re-run, an existing
App, and the stops. Nothing real is created, downloaded or started."""

import stat
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from core.config import Settings
from core.github.manifest import AppCredentials
from core.setup.checks import Ran
from core.setup.envfile import read_env, set_env
from core.setup.setup import AppInfo, SetupStopped, Tools, setup

EXAMPLE = """\
# --- Infrastructure
DATABASE_URL=postgresql+asyncpg://codelens:codelens@localhost:5433/codelens
API_JWT_SECRET=
# The accounts whose installations are reviewed (D119).
GITHUB_ALLOWED_ACCOUNTS=
GITHUB_APP_ID=
"""

CREDS = AppCredentials(
    app_id=42,
    slug="codelens-friend",
    html_url="https://github.com/apps/codelens-friend",
    pem="-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----\n",
    webhook_secret="whsec",
    client_id="Iv1.x",
    client_secret="cs",
    owner="Friend",
    owner_id=4242,
)


class Machine:
    """Everything setup touches, recorded."""

    def __init__(self, root: Path, *, pulled: list[str], ollama_up: bool = True) -> None:
        self.root = root
        self.pulled = set(pulled)
        self.ollama_up = ollama_up
        self.docker_up = True
        self.healthy_after = 2  # /health answers on the 3rd poll
        self.polls = 0
        self.commands: list[list[str]] = []
        self.channels = 0
        self.apps: list[tuple[str, str]] = []
        self.said: list[str] = []
        self.answers = {"username": "friend"}
        self.confirmed = True

    def run(self, cmd: Sequence[str]) -> Ran:
        if cmd[:2] == ["docker", "info"]:
            return Ran(0, "29.8.0") if self.docker_up else Ran(1, "daemon not running")
        return Ran(127, missing=True)

    def stream(self, cmd: Sequence[str]) -> int:
        self.commands.append(list(cmd))
        if cmd[:2] == ["ollama", "pull"]:
            self.pulled.add(cmd[2])
        return 0

    def get(self, url: str) -> httpx.Response:
        request = httpx.Request("GET", url)
        if url.endswith("/api/tags"):
            if not self.ollama_up:
                raise httpx.ConnectError("refused", request=request)
            models = [{"name": m} for m in sorted(self.pulled)]
            return httpx.Response(200, json={"models": models}, request=request)
        if url.endswith("/health"):
            self.polls += 1
            if self.polls <= self.healthy_after:
                raise httpx.ConnectError("not yet", request=request)
            return httpx.Response(200, json={"status": "ok"}, request=request)
        raise AssertionError(url)

    def ask(self, question: str, default: str | None) -> str:
        return self.answers["username"] if "username" in question else (default or "")

    def new_channel(self) -> str:
        self.channels += 1
        return f"https://smee.io/channel{self.channels}"

    def create_app(self, name: str, webhook_url: str) -> tuple[AppCredentials, Path, Path]:
        self.apps.append((name, webhook_url))
        secrets_dir = self.root / "secrets"
        secrets_dir.mkdir(exist_ok=True)
        key = secrets_dir / "github-app.pem"
        key.write_text(CREDS.pem)
        env = secrets_dir / "github-app.env"
        env.write_text(
            "GITHUB_APP_ID=42\nGITHUB_APP_PRIVATE_KEY_PATH=secrets/github-app.pem\n"
            "GITHUB_WEBHOOK_SECRET=whsec\nGITHUB_ALLOWED_ACCOUNTS=Friend\n"
            "GITHUB_CLIENT_ID=Iv1.x\nGITHUB_CLIENT_SECRET=cs\n"
        )
        return CREDS, key, env

    def tools(self, app: AppInfo | None = None) -> Tools:
        return Tools(
            run=self.run,
            stream=self.stream,
            get=self.get,
            ask=self.ask,
            confirm=lambda question: self.confirmed,
            new_smee_channel=self.new_channel,
            create_app=self.create_app,
            app_info=lambda settings: app,  # type: ignore[arg-type,return-value]
            say=self.said.append,
            sleep=lambda s: None,
            # The tests' own settings: .env values only, never this machine's.
            settings_for=lambda env: Settings(_env_file=env),  # type: ignore[call-arg]
        )


@pytest.fixture
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".env.example").write_text(EXAMPLE)
    # Settings would also read real environment variables; keep them out.
    for name in ("GITHUB_APP_ID", "GITHUB_ALLOWED_ACCOUNTS", "API_JWT_SECRET", "SMEE_URL"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_a_fresh_clone_ends_with_an_install_link(clone: Path) -> None:
    machine = Machine(clone, pulled=[])
    link = setup(clone, machine.tools())

    env = read_env(clone / ".env")
    assert link == "https://github.com/apps/codelens-friend/installations/new"
    assert len(env["API_JWT_SECRET"]) >= 32
    assert env["SMEE_URL"] == "https://smee.io/channel1"
    # The App was made for the relay's channel, named after the user.
    assert machine.apps == [("CodeLens (friend)", "https://smee.io/channel1")]
    assert (env["GITHUB_APP_ID"], env["GITHUB_WEBHOOK_SECRET"]) == ("42", "whsec")
    # The owner, so their PRs count, pinned to its id (D136).
    assert env["GITHUB_ALLOWED_ACCOUNTS"] == "Friend:4242"
    # The comments of .env.example are kept: .env is also the documentation.
    assert "# The accounts whose installations are reviewed" in (clone / ".env").read_text()
    assert machine.commands == [
        ["ollama", "pull", "qwen3:8b"],
        ["ollama", "pull", "qwen3-embedding:0.6b"],
        ["docker", "compose", "--profile", "relay", "up", "--build", "-d"],
    ]


def test_running_it_again_does_only_what_is_missing(clone: Path) -> None:
    first = Machine(clone, pulled=[])
    setup(clone, first.tools())
    secret = read_env(clone / ".env")["API_JWT_SECRET"]

    again = Machine(clone, pulled=["qwen3:8b", "qwen3-embedding:0.6b"])
    existing = AppInfo("codelens-friend", CREDS.html_url, "Friend", "https://smee.io/channel1")
    link = setup(clone, again.tools(existing))

    assert link == "https://github.com/apps/codelens-friend/installations/new"
    assert (again.channels, again.apps) == (0, [])  # no second channel, no second App
    assert read_env(clone / ".env")["API_JWT_SECRET"] == secret  # never rotated behind your back
    assert again.commands == [["docker", "compose", "--profile", "relay", "up", "--build", "-d"]]


def test_an_existing_app_keeps_its_own_channel(clone: Path) -> None:
    # Set up before Phase 8 (like the dev machine): an App, no SMEE_URL in .env.
    (clone / ".env").write_text(EXAMPLE)
    set_env(clone / ".env", {"GITHUB_APP_ID": "7", "GITHUB_ALLOWED_ACCOUNTS": "Owner"})
    machine = Machine(clone, pulled=["qwen3:8b", "qwen3-embedding:0.6b"])
    existing = AppInfo(
        "codelens-owner", "https://github.com/apps/codelens-owner", "Owner", "https://smee.io/old"
    )

    setup(clone, machine.tools(existing))

    env = read_env(clone / ".env")
    assert env["SMEE_URL"] == "https://smee.io/old"  # read from GitHub, not replaced
    assert machine.channels == 0
    assert env["GITHUB_ALLOWED_ACCOUNTS"] == "Owner"  # already allowed: unchanged


def test_an_existing_app_on_another_tunnel_runs_without_the_relay(clone: Path) -> None:
    (clone / ".env").write_text(EXAMPLE)
    set_env(clone / ".env", {"GITHUB_APP_ID": "7"})
    machine = Machine(clone, pulled=["qwen3:8b", "qwen3-embedding:0.6b"])
    existing = AppInfo("app", "https://github.com/apps/app", "Owner", "https://hooks.example.com/x")

    setup(clone, machine.tools(existing))

    assert "SMEE_URL" not in read_env(clone / ".env")
    assert machine.commands == [["docker", "compose", "up", "--build", "-d"]]
    assert read_env(clone / ".env")["GITHUB_ALLOWED_ACCOUNTS"] == "Owner"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda m: setattr(m, "ollama_up", False), "Ollama isn't reachable"),
        (lambda m: setattr(m, "confirmed", False), "ollama pull qwen3:8b && ollama pull"),
        (lambda m: setattr(m, "docker_up", False), "Docker isn't running"),
        (lambda m: setattr(m, "healthy_after", 10_000), "the API didn't come up"),
    ],
    ids=["no-ollama", "declined-download", "no-docker", "never-healthy"],
)
def test_it_stops_with_what_to_do(clone: Path, change, message: str) -> None:
    machine = Machine(clone, pulled=[])
    change(machine)
    with pytest.raises(SetupStopped, match=message):
        setup(clone, machine.tools())


def test_it_must_run_from_the_repo_root(tmp_path: Path) -> None:
    with pytest.raises(SetupStopped, match="repo root"):
        setup(tmp_path, Machine(tmp_path, pulled=[]).tools())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_env_is_owner_only(clone: Path) -> None:
    # Phase 9 step 6 (D132): .env was copied from .env.example with its 0644
    # and then given the JWT secret, readable by every local user.
    setup(clone, Machine(clone, pulled=[]).tools())
    assert stat.S_IMODE((clone / ".env").stat().st_mode) == 0o600

    # A .env from before D132 is narrowed on the next run.
    (clone / ".env").chmod(0o644)
    existing = AppInfo("codelens-friend", CREDS.html_url, "Friend", "https://smee.io/channel1")
    setup(clone, Machine(clone, pulled=["qwen3:8b", "qwen3-embedding:0.6b"]).tools(existing))
    assert stat.S_IMODE((clone / ".env").stat().st_mode) == 0o600

    # And set_env never widens it: the file it writes is a new, owner-only one.
    other = clone / "other.env"
    other.write_text("A=1\n")
    other.chmod(0o644)
    set_env(other, {"B": "2"})
    assert stat.S_IMODE(other.stat().st_mode) == 0o600
    assert not [p for p in clone.iterdir() if p.name.endswith(".tmp")]


def test_set_env_keeps_lines_and_appends_new_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\nA=1\nexport B='two'\n\nC=\n")
    set_env(env, {"B": "2", "D": "4"})
    assert env.read_text() == "# comment\nA=1\nB=2\n\nC=\nD=4\n"
    assert read_env(env) == {"A": "1", "B": "2", "C": "", "D": "4"}


def test_the_app_key_path_is_made_relative_for_the_containers(clone: Path) -> None:
    # The manifest flow writes the path it's given; setup passes an absolute one.
    # Seen in the fresh-clone rehearsal: every index job failed on it.
    machine = Machine(clone, pulled=["qwen3:8b", "qwen3-embedding:0.6b"])
    real_create = machine.create_app

    def create_with_absolute_path(name: str, url: str):
        creds, key, env = real_create(name, url)
        set_env(env, {"GITHUB_APP_PRIVATE_KEY_PATH": key.resolve().as_posix()})
        return creds, key, env

    machine.create_app = create_with_absolute_path  # type: ignore[method-assign]
    setup(clone, machine.tools())
    assert read_env(clone / ".env")["GITHUB_APP_PRIVATE_KEY_PATH"] == "secrets/github-app.pem"


def test_rerunning_setup_repairs_an_absolute_key_path(clone: Path) -> None:
    (clone / ".env").write_text(EXAMPLE)
    (clone / "secrets").mkdir()
    key = clone / "secrets" / "github-app.pem"
    key.write_text("k")
    set_env(
        clone / ".env",
        {"GITHUB_APP_ID": "7", "GITHUB_APP_PRIVATE_KEY_PATH": key.resolve().as_posix()},
    )
    machine = Machine(clone, pulled=["qwen3:8b", "qwen3-embedding:0.6b"])
    existing = AppInfo("app", "https://github.com/apps/app", "Owner", "https://smee.io/x")

    setup(clone, machine.tools(existing))

    assert read_env(clone / ".env")["GITHUB_APP_PRIVATE_KEY_PATH"] == "secrets/github-app.pem"


def test_an_owner_listed_by_login_alone_gets_pinned(clone: Path) -> None:
    # Phase 9 step 10 (D136): a bare login matches whoever holds it now.
    (clone / ".env").write_text(EXAMPLE)
    set_env(clone / ".env", {"GITHUB_APP_ID": "7", "GITHUB_ALLOWED_ACCOUNTS": "friend,other"})
    existing = AppInfo("codelens-friend", CREDS.html_url, "Friend", "https://smee.io/x", 4242)
    machine = Machine(clone, pulled=["qwen3:8b", "qwen3-embedding:0.6b"])
    setup(clone, machine.tools(existing))

    assert read_env(clone / ".env")["GITHUB_ALLOWED_ACCOUNTS"] == "other,Friend:4242"
    assert "allowed accounts: pinned Friend:4242" in machine.said
