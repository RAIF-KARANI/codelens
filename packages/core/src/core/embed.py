"""Embed an indexed repository's code nodes (only the stale ones).

    python -m core.embed <repo_name> [--limit N]

<repo_name> is the name `core.index` stored it under, e.g. "local/my-app".
Re-running on an up-to-date repo writes nothing.
"""

import argparse
import asyncio
import sys

from core.config import get_settings
from core.db import create_engine
from core.embedding import EmbeddingError, EmbedReport, embed_repository
from core.providers import ProviderError, open_providers


async def _run(repo_name: str, limit: int | None) -> EmbedReport:
    settings = get_settings()
    engine = create_engine(settings.database_url, pooled=False)

    def progress(done: int, total: int) -> None:
        print(f"  embedded {done}/{total}", flush=True)

    try:
        async with open_providers(settings) as providers:
            return await embed_repository(
                engine,
                providers.embedder,
                repo_name,
                max_tokens=settings.embed_max_tokens,
                limit=limit,
                progress=progress,
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m core.embed", description=__doc__.split("\n")[0]
    )
    parser.add_argument("repo_name", help='as stored by core.index, e.g. "local/my-app"')
    parser.add_argument("--limit", type=int, help="embed at most N stale nodes (for trying it out)")
    args = parser.parse_args()

    try:
        report = asyncio.run(_run(args.repo_name, args.limit))
    except (EmbeddingError, ProviderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(
        f"\n{report.repo_name}: {report.nodes_total} nodes, "
        f"{report.up_to_date} already up to date\n"
        f"  embedded   {report.embedded} with {report.model}"
        f" ({report.truncated} truncated to the token cap)\n"
        f"  tokens     {report.input_tokens}\n"
        f"  time       {report.seconds:.1f}s ({report.nodes_per_second:.0f} nodes/s)"
    )
    if report.embedded == 0:
        print("\n  Nothing to do: every embedding is current.")


if __name__ == "__main__":
    main()
