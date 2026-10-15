from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

import api.checks
from api.main import create_app
from core.config import Settings, get_settings
from core.providers.ollama import OllamaClient

# Port 1 is never listening, so every connection fails.
UNREACHABLE = Settings(
    _env_file=None,
    database_url="postgresql+asyncpg://user:pass@127.0.0.1:1/db",
    redis_url="redis://127.0.0.1:1/0",
    ollama_base_url="http://127.0.0.1:1",
)


def test_health_returns_503_when_dependencies_are_down() -> None:
    # `with` runs the app's lifespan (creates the engine and Redis client).
    with TestClient(create_app(UNREACHABLE)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    for check in ("database", "redis", "ollama"):
        assert body["checks"][check]["ok"] is False
        assert body["checks"][check]["error"]
        # Only the exception type is exposed, never connection details.
        assert "127.0.0.1" not in body["checks"][check]["error"]


def fake_ollama(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Point the Ollama check at an in-process fake instead of a server."""

    class Faked(OllamaClient):
        def __init__(self, base_url: str, **kwargs: object) -> None:
            super().__init__(base_url, **kwargs, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]

    monkeypatch.setattr(api.checks, "OllamaClient", Faked)


def tags(*names: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json={"models": [{"name": n} for n in names]})


def up(**overrides: object) -> Settings:
    """Postgres and Redis as the tests run them; models through Ollama."""
    base = get_settings()
    values: dict[str, object] = {
        "database_url": base.database_url,
        "redis_url": base.redis_url,
        "llm_provider": "ollama",
        "embed_provider": "ollama",
        "llm_model": "qwen3:8b",
        "embed_model": "qwen3-embedding",
        **overrides,
    }
    return Settings(_env_file=None, **values)


@pytest.mark.integration
def test_health_is_ok_when_everything_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    # "qwen3-embedding" was pulled without a tag, so Ollama lists it as :latest.
    fake_ollama(monkeypatch, tags("qwen3:8b", "qwen3-embedding:latest"))
    with TestClient(create_app(up())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert all(body["checks"][c]["ok"] for c in ("database", "redis", "ollama"))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("handler", "error"),
    [
        (tags("qwen3:8b"), "ModelNotPulled"),  # the embedder is missing
        (lambda request: httpx.Response(500, text="oops"), "ProviderError"),
    ],
    ids=["model-missing", "ollama-erroring"],
)
def test_health_is_degraded_but_200_without_ollama(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
    error: str,
) -> None:
    # The API still serves pages and takes webhooks; restarting it wouldn't help.
    fake_ollama(monkeypatch, handler)
    with TestClient(create_app(up())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["ollama"] == {
        "ok": False,
        "latency_ms": pytest.approx(0, abs=2000),
        "error": error,
    }


@pytest.mark.integration
def test_fake_providers_need_no_ollama() -> None:
    settings = up(llm_provider="fake", embed_provider="fake", ollama_base_url="http://127.0.0.1:1")
    with TestClient(create_app(settings)) as client:
        body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["checks"]["ollama"] is None
