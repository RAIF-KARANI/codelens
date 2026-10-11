"""Embedding throughput on the configured Ollama: batch size x concurrency.

    uv run python scripts/bench_embed.py [repo_path] [--texts 512]

Embeds the same set of real code chunks (built from a parse of repo_path, no
DB needed) with every combination of batch size and number of concurrent
requests, and prints chunks/s and tokens/s. Run with nothing else on the GPU.
"""

import argparse
import asyncio
import time
from pathlib import Path

import httpx

from core.config import get_settings
from core.parser import parse_repository
from core.text.tokens import chars_for_tokens

BATCH_SIZES = (8, 32, 64, 128)
CONCURRENCY = (1, 2, 4)


def sample_texts(repo: Path, count: int, max_tokens: int) -> list[str]:
    result = parse_repository(repo)
    nodes = [n for f in result.files for n in f.nodes if n.kind.value != "module"]
    cap = chars_for_tokens(max_tokens)
    return [f"{n.signature or n.name}\n{n.code}"[:cap] for n in nodes[:count]]


async def run(
    client: httpx.AsyncClient, model: str, texts: list[str], batch: int, concurrency: int
) -> tuple[float, int]:
    batches = [texts[i : i + batch] for i in range(0, len(texts), batch)]
    queue: asyncio.Queue[list[str]] = asyncio.Queue()
    for b in batches:
        queue.put_nowait(b)
    tokens = 0

    async def worker() -> None:
        nonlocal tokens
        while not queue.empty():
            items = queue.get_nowait()
            response = await client.post("/api/embed", json={"model": model, "input": items})
            response.raise_for_status()
            tokens += response.json().get("prompt_eval_count", 0)

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return time.perf_counter() - started, tokens


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "repo", type=Path, nargs="?", default=Path(".venv/Lib/site-packages/sqlalchemy")
    )
    parser.add_argument("--texts", type=int, default=512)
    args = parser.parse_args()

    settings = get_settings()
    texts = sample_texts(args.repo, args.texts, settings.embed_max_tokens)
    print(f"{len(texts)} chunks from {args.repo}, model {settings.embed_model}")
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=600) as client:
        await run(client, settings.embed_model, texts[:16], 16, 1)  # warm-up: load the model
        print(f"{'batch':>6} {'conc':>5} {'seconds':>8} {'chunks/s':>9} {'tokens/s':>9}")
        for batch in BATCH_SIZES:
            for concurrency in CONCURRENCY:
                seconds, tokens = await run(client, settings.embed_model, texts, batch, concurrency)
                print(
                    f"{batch:>6} {concurrency:>5} {seconds:>8.1f} "
                    f"{len(texts) / seconds:>9.1f} {tokens / seconds:>9.0f}"
                )


if __name__ == "__main__":
    asyncio.run(main())
