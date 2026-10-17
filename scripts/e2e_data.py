"""Load the demo export into an empty database for the dashboard's end-to-end
tests (planning-v2.md Phase 10 step 8).

    uv run python scripts/e2e_data.py apps/web/e2e/fixtures/codelens-demo.json.gz \
        --database-url postgresql+asyncpg://codelens:codelens@localhost:5433/codelens_e2e

An export only loads into a database at its own schema revision (core.demo).
So this migrates to the export's revision, imports it, then migrates to head:
the fixture keeps working after a new migration, and the tests also run the
data through every migration since it was made.
"""

import argparse
import asyncio
import gzip
import json
import sys
from pathlib import Path

from alembic import command

from core.db import create_engine
from core.db.migrate import alembic_config
from core.demo import DemoError, import_


def revision_of(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)["revision"]


async def load(path: Path, database_url: str) -> None:
    engine = create_engine(database_url, pooled=False)
    try:
        summary = await import_(engine, path)
    finally:
        await engine.dispose()
    print(f"imported {', '.join(summary.repos)}: {sum(summary.rows.values()):,} rows")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--database-url", required=True, help="an empty database (asyncpg URL)")
    args = parser.parse_args()

    config = alembic_config(args.database_url)
    revision = revision_of(args.file)
    command.upgrade(config, revision)
    try:
        asyncio.run(load(args.file, args.database_url))
    except DemoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    command.upgrade(config, "head")
    print(f"migrated {revision} -> head")
    return 0


if __name__ == "__main__":
    sys.exit(main())
