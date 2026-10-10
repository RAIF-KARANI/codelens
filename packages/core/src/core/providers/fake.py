"""Deterministic, offline providers for tests and CI (no GPU, no network).

FakeEmbedder hashes words into a fixed-size vector, so texts that share
identifiers land close together. That's enough to exercise retrieval plumbing
(SQL, ranking, packing) without a model, but says nothing about real quality.
"""

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from core.providers.base import Embeddings, EmbedKind, LLMResult, Message, Usage
from core.providers.ollama import l2_normalize

_WORDS = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def words(text: str) -> list[str]:
    """getUserById / get_user_by_id -> ['get', 'user', 'by', 'id']."""
    return [w.lower() for w in _WORDS.findall(text)]


class FakeEmbedder:
    model = "fake-embedder"

    def __init__(self, dim: int) -> None:
        self.dim = dim

    async def embed(self, texts: Sequence[str], *, kind: EmbedKind) -> Embeddings:
        vectors = [self._vector(text) for text in texts]
        tokens = sum(len(words(t)) for t in texts)
        return Embeddings(vectors, self.model, Usage(input_tokens=tokens))

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for word in words(text):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0  # signed hashing reduces collision bias
            vector[index] += sign
        if not any(vector):
            vector[0] = 1.0  # empty text still gets a valid unit vector
        return l2_normalize(vector)


class FakeReranker:
    model = "fake-reranker"

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        """Jaccard overlap of words: crude, deterministic, monotonic in overlap."""
        q = set(words(query))
        scores = []
        for doc in documents:
            d = set(words(doc))
            scores.append(len(q & d) / len(q | d) if q | d else 0.0)
        return scores


Responder = Callable[[Sequence[Message], type[BaseModel]], BaseModel | dict[str, object] | str]


@dataclass(frozen=True, slots=True)
class FakeCall:
    messages: tuple[Message, ...]
    schema: type[BaseModel]
    think: bool


@dataclass
class FakeLLM:
    """Returns whatever `responder` produces, validated against the schema
    exactly like a real provider's output. Records every call for assertions."""

    responder: Responder | None = None
    model: str = "fake-llm"
    calls: list[FakeCall] = field(default_factory=list)

    async def generate_structured[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        think: bool = False,
        max_output_tokens: int | None = None,
    ) -> LLMResult[T]:
        self.calls.append(FakeCall(tuple(messages), schema, think))
        produced = self.responder(messages, schema) if self.responder else {}
        if isinstance(produced, str):
            output = schema.model_validate_json(produced)
        elif isinstance(produced, BaseModel):
            output = schema.model_validate(produced.model_dump())
        else:
            output = schema.model_validate(produced)
        raw = output.model_dump_json()
        prompt_tokens = sum(len(words(m.content)) for m in messages)
        return LLMResult(output, self.model, Usage(prompt_tokens, len(words(raw))), raw)
