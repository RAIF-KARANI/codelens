import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from core.config import Settings

# Port 1 is never listening, so both connections fail.
UNREACHABLE = Settings(
    _env_file=None,
    database_url="postgresql+asyncpg://user:pass@127.0.0.1:1/db",
    redis_url="redis://127.0.0.1:1/0",
)


def test_health_returns_503_when_dependencies_are_down() -> None:
    # `with` runs the app's lifespan (creates the engine and Redis client).
    with TestClient(create_app(UNREACHABLE)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    for check in ("database", "redis"):
        assert body["checks"][check]["ok"] is False
        assert body["checks"][check]["error"]
        # Only the exception type is exposed, never connection details.
        assert "127.0.0.1" not in body["checks"][check]["error"]


@pytest.mark.integration
def test_health_returns_200_when_dependencies_are_up() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["redis"]["ok"] is True
