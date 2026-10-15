"""The public demo's API on Vercel (deploy/api/README.md).

The same FastAPI app as everywhere else (services/api), run with DEMO_MODE=true:
read-only, on Postgres alone, no Redis or model (docs/demo.md). Vercel finds
`app` here, installs this folder's pyproject.toml from its uv.lock, and runs
the app's lifespan, which creates the database engine.
"""

import os

# Vercel's log view searches and filters JSON lines by field (D121); a
# LOG_FORMAT set on the project still wins. Set before the app reads settings.
os.environ.setdefault("LOG_FORMAT", "json")

from api.main import app

__all__ = ["app"]
