"""Search an indexed + embedded repository by meaning.

    python -m core.search <repo_name> "query" [--k 10] [--kinds function,method] [--explain]

The query is embedded as a *query* (with the retrieval instruction), then
matched against node embeddings by cosine distance. --explain prints the
Postgres plan, which shows whether the HNSW index was used.
"""

import argparse
import asyncio
import sys
import time

from sqlalchemy import select, text

from core.config import get_settings
from core.db import create_engine
from core.db.models import Repo
from core.enums import NodeKind
from core.providers import EmbedKind, ProviderError, open_providers
from core.retrieval import SearchHit, vector_search
from core.retrieval.vector import explain_vector_search


class SearchError(Exception):
    pass


async def _run(
    repo_name: str, query: str, k: int, kinds: set[NodeKind] | None, explain: bool
) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pooled=False)
    try:
        async with engine.connect() as conn:
            repo_id = await conn.scalar(select(Repo.id).where(Repo.full_name == repo_name))
            if repo_id is None:
                raise SearchError(f"no indexed repo named {repo_name!r}")
            embedded = await conn.scalar(
                text(
                    "SELECT count(*) FROM code_nodes WHERE repo_id = :r AND embedding IS NOT NULL"
                ),
                {"r": repo_id},
            )
            await conn.commit()
            if not embedded:
                raise SearchError(f"{repo_name} has no embeddings yet; run core.embed first")

            async with open_providers(settings) as providers:
                started = time.perf_counter()
                embedding = await providers.embedder.embed([query], kind=EmbedKind.QUERY)
                embed_s = time.perf_counter() - started

            vector = embedding.vectors[0]
            started = time.perf_counter()
            hits = await vector_search(conn, repo_id=repo_id, query_vector=vector, k=k, kinds=kinds)
            search_s = time.perf_counter() - started

            print(f'Top {len(hits)} for "{query}" in {repo_name} ({embedded} embedded nodes)')
            print(f"  embed query {embed_s * 1000:.0f} ms, search {search_s * 1000:.0f} ms\n")
            _print_hits(hits)

            if explain:
                plan = await explain_vector_search(
                    conn, repo_id=repo_id, query_vector=vector, k=k, kinds=kinds, analyze=True
                )
                print("\nQuery plan (EXPLAIN ANALYZE):")
                for line in plan:
                    print(f"  {line}")
    finally:
        await engine.dispose()


def _print_hits(hits: list[SearchHit]) -> None:
    for hit in hits:
        name = hit.qualified_name + (f"#{hit.ordinal}" if hit.ordinal else "")
        print(f"  {hit.rank:>2}. {hit.similarity:5.3f}  {hit.kind.value:<8} {name}")
        print(
            f"      {hit.path}:{hit.start_line}-{hit.end_line}"
            + (f"  {hit.signature}" if hit.signature else "")
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m core.search", description=__doc__.split("\n")[0]
    )
    parser.add_argument("repo_name", help='as stored by core.index, e.g. "local/my-app"')
    parser.add_argument("query", help="natural language or code")
    parser.add_argument("--k", type=int, default=10, help="number of results (default 10)")
    parser.add_argument(
        "--kinds",
        help="comma-separated node kinds to keep: module,class,function,method",
    )
    parser.add_argument("--explain", action="store_true", help="print the Postgres query plan")
    args = parser.parse_args()

    kinds = {NodeKind(k.strip()) for k in args.kinds.split(",")} if args.kinds else None
    try:
        asyncio.run(_run(args.repo_name, args.query, args.k, kinds, args.explain))
    except (SearchError, ProviderError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
