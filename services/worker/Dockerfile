# Same two-layer pattern as services/api/Dockerfile; see comments there.
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# git is needed later for cloning repos to index.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/
COPY services/api/pyproject.toml services/api/
COPY services/worker/pyproject.toml services/worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package codelens-worker --no-install-workspace

COPY packages/core packages/core
COPY services/worker services/worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package codelens-worker

# Celery warns when run as root; a worker that executes jobs over untrusted
# repos should run unprivileged anyway.
RUN useradd --create-home --uid 1000 app
USER app

CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=INFO"]
