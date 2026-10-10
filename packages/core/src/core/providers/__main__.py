"""Check the configured model providers end to end (no DB involved).

    python -m core.providers

Verifies: Ollama is reachable, both models are pulled, the embedding
dimension matches EMBED_DIM, query/document embeddings rank sensibly, and the
LLM returns schema-valid JSON. Prints latency and token throughput. Exits 1 if
anything fails.
"""

import asyncio
import sys
import time

from pydantic import BaseModel, Field

from core.config import Settings, get_settings
from core.providers import EmbedKind, Message, ProviderError, open_providers
from core.providers.ollama import OllamaClient


class _Probe(BaseModel):
    """Tiny schema to prove structured output works."""

    bug: str = Field(description="One-sentence description of the bug")
    severity: str = Field(pattern="^(critical|warning|suggestion)$")


_CODE = "def average(xs):\n    return sum(xs) / len(xs)\n"


async def _check(settings: Settings) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    if settings.llm_provider != "ollama" and settings.embed_provider != "ollama":
        return [(True, "providers are fakes; nothing to check")]

    async with OllamaClient(settings.ollama_base_url, timeout_s=10, max_retries=0) as client:
        try:
            version = await client.version()
            pulled = await client.models()
        except ProviderError as exc:
            return [(False, str(exc))]
    results.append((True, f"Ollama {version} at {settings.ollama_base_url}"))
    for model in (settings.llm_model, settings.embed_model):
        present = model in pulled or f"{model}:latest" in pulled
        results.append(
            (
                present,
                f"model {model} "
                + ("pulled" if present else f"MISSING: run `ollama pull {model}`"),
            )
        )
    if not all(ok for ok, _ in results):
        return results

    async with open_providers(settings) as providers:
        start = time.perf_counter()
        docs = await providers.embedder.embed(
            [_CODE, "def render_header(title):\n    return f'<h1>{title}</h1>'\n"],
            kind=EmbedKind.DOCUMENT,
        )
        query = await providers.embedder.embed(
            ["division by zero when the list is empty"], kind=EmbedKind.QUERY
        )
        elapsed = time.perf_counter() - start
        dim = len(docs.vectors[0])
        sims = [sum(a * b for a, b in zip(query.vectors[0], d, strict=True)) for d in docs.vectors]
        results.append(
            (
                dim == settings.embed_dim,
                f"embeddings {dim}-d (EMBED_DIM={settings.embed_dim}), {elapsed:.2f}s",
            )
        )
        results.append(
            (
                sims[0] > sims[1],
                f"query ranks the related function first ({sims[0]:.3f} vs {sims[1]:.3f})",
            )
        )

        messages = [
            Message("system", "You are a code reviewer. Answer in JSON."),
            Message("user", f"Find the most serious bug:\n```python\n{_CODE}```"),
        ]
        # Twice: the first call includes loading the model into VRAM, the
        # second shows steady-state latency.
        for label in ("cold", "warm"):
            start = time.perf_counter()
            try:
                result = await providers.llm.generate_structured(
                    messages, _Probe, max_output_tokens=200
                )
            except ProviderError as exc:
                results.append((False, f"structured generation failed: {exc}"))
                return results
            elapsed = time.perf_counter() - start
            results.append(
                (
                    True,
                    f"LLM {result.model} ({label}): valid JSON in {elapsed:.1f}s, "
                    f"{result.usage.input_tokens} in / {result.usage.output_tokens} out tokens "
                    f"-> {result.output.severity}: {result.output.bug}",
                )
            )
    return results


def main() -> None:
    results = asyncio.run(_check(get_settings()))
    for ok, line in results:
        print(f"  [{'ok' if ok else 'FAIL'}] {line}")
    if not all(ok for ok, _ in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
