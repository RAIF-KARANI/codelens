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


def test_jwt_secret_is_empty_or_at_least_32_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).api_jwt_secret.get_secret_value() == ""  # unset: API refuses
    monkeypatch.setenv("API_JWT_SECRET", "x" * 32)
    assert Settings(_env_file=None).api_jwt_secret.get_secret_value() == "x" * 32

    monkeypatch.setenv("API_JWT_SECRET", "too-short")
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        Settings(_env_file=None)


def test_empty_variables_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # As in .env.example: `GITHUB_APP_ID=` must not fail to parse as a number.
    monkeypatch.setenv("GITHUB_APP_ID", "")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    settings = Settings(_env_file=None)
    assert settings.github_app_id is None
    assert settings.github_webhook_secret.get_secret_value() == ""


def test_admin_logins_are_a_case_insensitive_list(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).admin_logins == frozenset()
    monkeypatch.setenv("API_ADMIN_LOGINS", " RAIF-KARANI, octo ,,")
    assert Settings(_env_file=None).admin_logins == {"raif-karani", "octo"}
