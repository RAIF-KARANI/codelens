"""Model providers behind small interfaces: embeddings, reranking, LLM generation.

async with open_providers(get_settings()) as providers:
    result = await providers.llm.generate_structured(messages, MySchema)
"""

from core.providers.base import (
    LLM,
    Embedder,
    Embeddings,
    EmbedKind,
    LLMResult,
    Message,
    ProviderError,
    ProviderUnavailableError,
    Reranker,
    StructuredOutputError,
    Usage,
)
from core.providers.factory import Providers, open_providers

__all__ = [
    "LLM",
    "EmbedKind",
    "Embedder",
    "Embeddings",
    "LLMResult",
    "Message",
    "ProviderError",
    "ProviderUnavailableError",
    "Providers",
    "Reranker",
    "StructuredOutputError",
    "Usage",
    "open_providers",
]
