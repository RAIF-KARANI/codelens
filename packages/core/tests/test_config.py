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


def test_allowed_accounts_are_a_case_insensitive_set(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not Settings(_env_file=None).allowed_accounts  # unset: nobody
    monkeypatch.setenv("GITHUB_ALLOWED_ACCOUNTS", " RAIF-KARANI, a-friend:42 ,,")
    accounts = Settings(_env_file=None).allowed_accounts
    assert accounts.pins == {"raif-karani": None, "a-friend": 42}
    assert accounts.unpinned == ["raif-karani"]


def test_a_pinned_account_matches_only_its_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # Phase 9 step 10 (D136): entries matched logins only, and a login an
    # account gives up can be registered by someone else.
    monkeypatch.setenv("GITHUB_ALLOWED_ACCOUNTS", "octo:7,friend")
    accounts = Settings(_env_file=None).allowed_accounts
    assert accounts.allows("Octo", 7)
    assert not accounts.allows("octo", 8)  # the login, under another account
    assert accounts.allows("friend", 123)  # unpinned: by login, as before
    assert not accounts.allows("mallory", 9)
    assert not accounts.allows(None, 7)


def test_a_bad_account_id_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ALLOWED_ACCOUNTS", "octo:seven")
    with pytest.raises(ValidationError, match="an account id is a number"):
        Settings(_env_file=None)


def test_empty_variables_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # As in .env.example: `GITHUB_APP_ID=` must not fail to parse as a number.
    monkeypatch.setenv("GITHUB_APP_ID", "")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    settings = Settings(_env_file=None)
    assert settings.github_app_id is None
    assert settings.github_webhook_secret.get_secret_value() == ""


def test_admin_logins_are_a_case_insensitive_list(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not Settings(_env_file=None).admins
    monkeypatch.setenv("API_ADMIN_LOGINS", " RAIF-KARANI, octo:1001 ,,")
    assert Settings(_env_file=None).admins.pins == {"raif-karani": None, "octo": 1001}
