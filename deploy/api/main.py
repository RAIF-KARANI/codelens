"""The public demo's API on Vercel (deploy/api/README.md).

The same FastAPI app as everywhere else (services/api), run with DEMO_MODE=true:
read-only, on Postgres alone, no Redis or model (docs/demo.md). Vercel finds
`app` here, installs requirements.txt, and runs the app's lifespan, which
creates the database engine.
"""

from api.main import app

__all__ = ["app"]
