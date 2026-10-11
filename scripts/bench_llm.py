"""LLM speed and VRAM for one context-window setting on one Ollama server.

    uv run python scripts/bench_llm.py --num-ctx 8192 [--prompt-tokens 5500]
    OLLAMA_BASE_URL=http://localhost:11435 uv run python scripts/bench_llm.py --num-ctx 16384

Sends a review-sized prompt (real code from this repo) with the ReviewOut JSON
schema, twice (cold: includes loading; warm), and reports load, prompt
processing and decode rates from Ollama's own timings, plus how much of the
model sits in VRAM (/api/ps).
"""

import argparse
import asyncio
from pathlib import Path

import httpx

from core.config import get_settings
from core.review.schemas import ReviewOut
from core.text.tokens import chars_for_tokens


def build_prompt(tokens: int) -> str:
    code = "\n\n".join(
        p.read_text(encoding="utf-8") for p in sorted(Path("packages/core/src/core").rglob("*.py"))
    )
    return (
        "Review the following code for bugs. Return JSON.\n\n<context>\n"
        + code[: chars_for_tokens(tokens)]
        + "\n</context>"
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-ctx", type=int, required=True)
    parser.add_argument("--prompt-tokens", type=int, default=5500)
    parser.add_argument("--max-output", type=int, default=400)
    args = parser.parse_args()

    settings = get_settings()
    prompt = build_prompt(args.prompt_tokens)
    body = {
        "model": settings.llm_model,
        "messages": [{"role": "user", "content": prompt}],
        "format": ReviewOut.model_json_schema(),
        "think": False,
        "stream": False,
        "options": {
            "num_ctx": args.num_ctx,
            "temperature": 0.1,
            "seed": 42,
            "num_predict": args.max_output,
        },
    }
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=900) as client:
        for label in ("cold", "warm"):
            r = (await client.post("/api/chat", json=body)).json()
            ps = (await client.get("/api/ps")).json()["models"]
            model = next(m for m in ps if m["name"].startswith(settings.llm_model))
            gpu_share = model["size_vram"] / model["size"] if model["size"] else 0
            # A warm run re-uses Ollama's prompt cache for the identical prompt,
            # so its prompt rate is not real throughput; read the cold one.
            prompt_rate = r["prompt_eval_count"] / (r["prompt_eval_duration"] / 1e9)
            decode_rate = r["eval_count"] / (r["eval_duration"] / 1e9)
            print(
                f"{label}: num_ctx {args.num_ctx}  model {model['size'] / 2**30:.1f} GiB "
                f"({gpu_share:.0%} in VRAM)  load {r.get('load_duration', 0) / 1e9:.1f}s  "
                f"prompt {r['prompt_eval_count']} tok @ {prompt_rate:.0f} tok/s  "
                f"decode {r['eval_count']} tok @ {decode_rate:.1f} tok/s  "
                f"total {r['total_duration'] / 1e9:.1f}s"
            )


if __name__ == "__main__":
    asyncio.run(main())
