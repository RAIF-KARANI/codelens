"""Programmatic access to Alembic (used by tests and tooling).

From the shell, use the CLI instead:
    uv run alembic -c packages/core/alembic.ini upgrade head
"""

from pathlib import Path

from alembic.config import Config

# packages/core/src/core/db/migrate.py -> packages/core/alembic.ini
ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


def alembic_config(database_url: str | None = None) -> Config:
    """Alembic config, optionally targeting a specific database URL.

    Without a URL, env.py falls back to DATABASE_URL from settings.
    """
    config = Config(str(ALEMBIC_INI))
    if database_url is not None:
        # Config values go through configparser, where '%' starts an
        # interpolation; escape it so passwords containing '%' survive.
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config
