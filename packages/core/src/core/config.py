"""Application settings, loaded from environment variables (and `.env` if present).

All services import `get_settings()` from here, so config lives in exactly one place.
"""

from functools import lru_cache
from typing import Literal

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

    # --- model providers ("fake" = deterministic, offline; used by tests/CI)
    llm_provider: Literal["ollama", "fake"] = "ollama"
    embed_provider: Literal["ollama", "fake"] = "ollama"
    # Containers reach a host-native Ollama at http://host.docker.internal:11434.
    ollama_base_url: str = "http://localhost:11434"

    llm_model: str = "qwen3:8b"
    # Context window per request. 8k keeps qwen3:8b entirely in 8 GB of VRAM
    # with Ollama's default fp16 KV cache; with OLLAMA_FLASH_ATTENTION=1 and
    # OLLAMA_KV_CACHE_TYPE=q8_0 on the Ollama server, 16384 fits too
    # (planning.md §1.3, D57). The review pipeline fits each prompt to this.
    llm_num_ctx: int = Field(default=8192, ge=2048)
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    llm_seed: int = 42  # fixed for reproducible evals
    llm_max_output_tokens: int = Field(default=2000, gt=0)
    llm_timeout_s: float = Field(default=300.0, gt=0)

    embed_model: str = "qwen3-embedding:0.6b"
    embed_batch_size: int = Field(default=32, gt=0)
    # Cap on chunk text per node (planning.md §5.6); longer code is truncated
    # at a line boundary and marked.
    embed_max_tokens: int = Field(default=1024, ge=64)
    # Qwen3-Embedding is instruction-aware: queries get a task instruction,
    # documents are embedded as-is (asymmetric retrieval, planning.md D23).
    embed_query_instruction: str = (
        "Given a code change, retrieve code that it depends on or that depends on it"
    )
    # The embedder's context window. Chunks are capped at embed_max_tokens, so
    # 2048 leaves headroom while keeping the embedder at ~2 GiB. That's small
    # enough to sit in VRAM beside qwen3:8b with no slowdown; at Ollama's
    # default 4096 (2.4 GiB) the two overflowed 8 GB and decode fell from 31
    # to 8 tok/s (planning.md D62).
    embed_num_ctx: int = Field(default=2048, ge=512)
    # Embed queries on CPU instead (Ollama num_gpu=0). Off: measured ~20x
    # slower than GPU for review-sized queries (187 s vs 9 s for 24 queries).
    # Only worth it on GPUs too small to hold both models.
    embed_query_on_cpu: bool = False
    embed_timeout_s: float = Field(default=120.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is parsed once per process."""
    return Settings()
