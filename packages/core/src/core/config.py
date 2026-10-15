"""Application settings, loaded from environment variables (and `.env` if present).

All services import `get_settings()` from here, so config lives in exactly one place.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: `GITHUB_APP_ID=` (as copied from .env.example) means
    # "not set", i.e. the default, instead of failing to parse "" as a number.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # Async driver URL; SQLAlchemy uses asyncpg through the `+asyncpg` dialect.
    database_url: str = "postgresql+asyncpg://codelens:codelens@localhost:5433/codelens"
    # DATABASE_URL goes through a pooler in transaction mode (Supabase's port
    # 6543): the hosted demo's API, where many short-lived instances share one
    # database (docs/demo.md). Turns off asyncpg's prepared-statement caches.
    database_transaction_pooler: bool = False
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

    # The pipeline the worker reviews with. Relative paths resolve against the
    # working directory: the repo root locally, /app in the containers.
    pipeline_config: Path = Path("configs/pipeline/default.yaml")

    # --- API (planning.md §7)
    # Signs the dashboard's HS256 tokens. Unset means the API refuses every
    # /api/v1 request: an unconfigured server must fail closed, not open.
    api_jwt_secret: SecretStr = SecretStr("")
    # A public read-only instance (§15): every mutating endpoint returns 403.
    demo_mode: bool = False
    # GitHub logins (comma-separated) that may see every repo, including local
    # and eval ones with no installation (D111). Everyone else sees only the
    # repos of the App installations their token lists.
    api_admin_logins: str = ""

    @property
    def admin_logins(self) -> frozenset[str]:
        # GitHub logins are case-insensitive.
        return frozenset(x.strip().lower() for x in self.api_admin_logins.split(",") if x.strip())

    # --- GitHub App (docs/github-app.md). `python -m core.github register` creates
    # the App and writes these to secrets/github-app.env.
    github_app_id: int | None = None
    github_app_private_key_path: Path = Path("secrets/github-app.pem")
    # Signs every webhook delivery (HMAC SHA-256). Unset means the webhook
    # endpoint refuses everything, like the API without its JWT secret.
    github_webhook_secret: SecretStr = SecretStr("")
    # The GitHub accounts (users or organisations, comma-separated) whose
    # installations CodeLens acts on: their repos are indexed and their pull
    # requests reviewed. Any other installation is ignored, so the App can be
    # installable by "Any account" without strangers' PRs running on this
    # machine's GPU. Unset means nobody: it fails closed like the secrets.
    github_allowed_accounts: str = ""
    # Pull request reviews one account may start per hour (0: no cap). The
    # one over the cap is recorded as failed, saying why, and not queued.
    github_reviews_per_hour: int = Field(default=10, ge=0)
    github_api_url: str = "https://api.github.com"
    # Clone URLs are "<github_web_url>/<owner>/<name>.git".
    github_web_url: str = "https://github.com"

    @property
    def allowed_accounts(self) -> frozenset[str]:
        # GitHub logins are case-insensitive.
        return frozenset(
            x.strip().lower() for x in self.github_allowed_accounts.split(",") if x.strip()
        )

    # --- storage: bare mirrors of GitHub repos live in DATA_DIR/repos/ (gitignored)
    data_dir: Path = Path("data")

    @field_validator("api_jwt_secret")
    @classmethod
    def _secret_long_enough(cls, secret: SecretStr) -> SecretStr:
        # HS256 keys shorter than the hash (32 bytes) can be brute-forced
        # offline from any one token (RFC 7518 §3.2).
        if 0 < len(secret.get_secret_value().encode()) < 32:
            raise ValueError("API_JWT_SECRET must be at least 32 bytes")
        return secret


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is parsed once per process."""
    return Settings()
