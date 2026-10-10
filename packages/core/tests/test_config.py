import pytest
from pydantic import ValidationError

from core.config import Settings


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_DIM", "768")
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/1")

    settings = Settings(_env_file=None)

    assert settings.embed_dim == 768
    assert settings.redis_url == "redis://example:6379/1"


def test_embed_dim_rejects_values_hnsw_cannot_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_DIM", "3072")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
