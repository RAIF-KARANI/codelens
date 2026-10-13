"""Move demo repos to the hosted demo's database (core/demo, planning.md §15).

python -m core.demo export --repo you/codelens-demo --out demo.json.gz
python -m core.demo import demo.json.gz --database-url postgresql+asyncpg://...

`export` reads this machine's database (DATABASE_URL). `import` writes to
--database-url, which must already be migrated to the same revision.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from core.config import get_settings
from core.db import create_engine
from core.demo import DemoError, Summary, export, github_public_check, import_


def _report(verb: str, summary: Summary) -> None:
    print(f"{verb} {', '.join(summary.repos)}:")
    for table, count in summary.rows.items():
        print(f"  {table:22} {count:7,}")


async def _export(args: argparse.Namespace) -> Summary:
    settings = get_settings()
    engine = create_engine(settings.database_url, pooled=False)
    try:
        return await export(
            engine, args.repo, args.out, is_public=github_public_check(settings.github_api_url)
        )
    finally:
        await engine.dispose()


async def _import(args: argparse.Namespace) -> Summary:
    engine = create_engine(args.database_url or get_settings().database_url, pooled=False)
    try:
        return await import_(engine, args.file)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m core.demo", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("export", help="write public repos' rows to a file")
    ex.add_argument("--repo", action="append", required=True, help="owner/name; repeat for more")
    ex.add_argument("--out", type=Path, default=Path("demo.json.gz"))
    im = sub.add_parser("import", help="load an export into a database, replacing those repos")
    im.add_argument("file", type=Path)
    im.add_argument(
        "--database-url", help="the demo's database (asyncpg URL); default: DATABASE_URL"
    )
    args = parser.parse_args()

    try:
        if args.command == "export":
            _report("Exported", asyncio.run(_export(args)))
            print(f"-> {args.out}")
        else:
            _report("Imported", asyncio.run(_import(args)))
    except DemoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
