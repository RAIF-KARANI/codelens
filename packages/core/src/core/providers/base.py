"""Provider-agnostic interfaces for embeddings, reranking, and LLM generation.

The retrieval and review code depends only on these Protocols. Concrete
providers (Ollama today; anything with an HTTP API tomorrow) and the
deterministic fakes used in tests are interchangeable behind them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel


class EmbedKind(StrEnum):
    """Many embedding models encode queries and documents differently (an
    instruction prefix for queries, nothing for documents)."""

    DOCUMENT = "document"
    QUERY = "query"


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.duration_ms + other.duration_ms,
        )


@dataclass(frozen=True, slots=True)
class Embeddings:
    vectors: list[list[float]]  # one per input text, L2-normalised
    model: str
    usage: Usage


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class LLMResult[T: BaseModel]:
    output: T  # already validated against the requested schema
    model: str
    usage: Usage
    raw: str  # the JSON text the model produced (kept for traces/debugging)
    thinking: str | None = None  # reasoning text, when thinking mode is on
    attempts: int = 1  # >1 when a repair retry was needed


class Embedder(Protocol):
    model: str
    dim: int

    async def embed(self, texts: Sequence[str], *, kind: EmbedKind) -> Embeddings:
        """Embed texts in order. Implementations batch internally."""
        ...


class Reranker(Protocol):
    model: str

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        """Relevance score per document (higher = more relevant), same order."""
        ...


class LLM(Protocol):
    model: str

    async def generate_structured[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        think: bool = False,
        max_output_tokens: int | None = None,
        sampling: Mapping[str, float] | None = None,
    ) -> LLMResult[T]:
        """Generate a response constrained to `schema` and validated against it.
        `sampling` overrides the provider's defaults (temperature, top_p, top_k)."""
        ...


class ProviderError(Exception):
    """A provider call failed in a way the caller may want to report."""


class ProviderUnavailableError(ProviderError):
    """The provider couldn't be reached (down, wrong URL, timed out)."""


class StructuredOutputError(ProviderError):
    """The model's output didn't validate against the schema, even after a repair attempt."""

    def __init__(self, message: str, raw: str, *, truncated: bool = False) -> None:
        super().__init__(message)
        self.raw = raw
        self.truncated = truncated  # hit the output-token limit before the JSON ended
