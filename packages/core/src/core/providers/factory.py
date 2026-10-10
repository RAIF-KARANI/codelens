"""Build providers from Settings. The rest of the code never names a vendor."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from core.config import Settings
from core.providers.base import LLM, Embedder
from core.providers.fake import FakeEmbedder, FakeLLM
from core.providers.ollama import OllamaClient, OllamaEmbedder, OllamaLLM


@dataclass(frozen=True, slots=True)
class Providers:
    embedder: Embedder
    llm: LLM


@asynccontextmanager
async def open_providers(settings: Settings) -> AsyncIterator[Providers]:
    """Providers sharing one HTTP connection pool, closed on exit.

    A context manager rather than a plain factory because the Ollama providers
    own an httpx client, which must be closed to release its connections.
    """
    embed_client = OllamaClient(settings.ollama_base_url, timeout_s=settings.embed_timeout_s)
    llm_client = OllamaClient(settings.ollama_base_url, timeout_s=settings.llm_timeout_s)
    try:
        embedder: Embedder
        if settings.embed_provider == "fake":
            embedder = FakeEmbedder(settings.embed_dim)
        else:
            embedder = OllamaEmbedder(
                embed_client,
                model=settings.embed_model,
                dim=settings.embed_dim,
                batch_size=settings.embed_batch_size,
                query_instruction=settings.embed_query_instruction,
                on_cpu=settings.embed_on_cpu,
            )
        llm: LLM
        if settings.llm_provider == "fake":
            llm = FakeLLM()
        else:
            llm = OllamaLLM(
                llm_client,
                model=settings.llm_model,
                num_ctx=settings.llm_num_ctx,
                temperature=settings.llm_temperature,
                seed=settings.llm_seed,
                max_output_tokens=settings.llm_max_output_tokens,
            )
        yield Providers(embedder, llm)
    finally:
        await embed_client.aclose()
        await llm_client.aclose()
