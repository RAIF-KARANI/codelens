"""Application settings, loaded from environment variables (and `.env` if present).

All services import `get_settings()` from here, so config lives in exactly one place.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Async driver URL; SQLAlchemy uses asyncpg through the `+asyncpg` dialect.
    database_url: str = "postgresql+asyncpg://codelens:codelens@localhost:5433/codelens"
    redis_url: str = "redis://localhost:6379/0"

    # pgvector stores a fixed dimension per column, so this value is baked into
    # the schema at migration time. Changing it later requires a new migration.
    # HNSW indexes on `vector` support at most 2000 dimensions.
    embed_dim: int = Field(default=1024, gt=0, le=2000)

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is parsed once per process."""
    return Settings()
