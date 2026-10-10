"""Index a local repository into Postgres.

    python -m core.index <repo_path> [--name owner/name]

Re-running on an unchanged repo writes nothing to files/nodes/edges; each run
is recorded in index_runs. Uses DATABASE_URL from the environment / .env.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from core.config import get_settings
from core.db import create_engine
from core.indexing import IndexingError, format_report, index_repository


async def _run(path: Path, name: str | None) -> None:
    # pooled=False: a CLI runs one event loop and exits; no pool to keep warm.
    engine = create_engine(get_settings().database_url, pooled=False)
    try:
        report = await index_repository(engine, path, name=name)
    finally:
        await engine.dispose()
    print(format_report(report))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m core.index", description="Index a local repository into Postgres."
    )
    parser.add_argument("path", type=Path, help="repository root")
    parser.add_argument("--name", help='repo name in the DB (default: "local/<dir name>")')
    args = parser.parse_args()
    logging.basicConfig(
        level=get_settings().log_level, format="%(levelname)s %(name)s: %(message)s"
    )

    try:
        asyncio.run(_run(args.path, args.name))
    except IndexingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
