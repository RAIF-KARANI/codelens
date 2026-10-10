"""Ollama providers (local models over HTTP): embeddings and structured chat.

API used:
  POST /api/embed  {model, input: [texts], options}
                   -> {embeddings, prompt_eval_count, total_duration (ns)}
  POST /api/chat   {model, messages, format: <JSON schema>, think, options, stream: false}
                   -> {message: {content, thinking?}, done_reason, prompt_eval_count,
                       eval_count, total_duration (ns)}
"""

import asyncio
import math
from collections.abc import Sequence
from typing import Any, Self

import httpx
from pydantic import BaseModel, ValidationError

from core.providers.base import (
    Embeddings,
    EmbedKind,
    LLMResult,
    Message,
    ProviderError,
    ProviderUnavailableError,
    StructuredOutputError,
    Usage,
)


class OllamaClient:
    """Thin async HTTP wrapper with retries for transient failures."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float,
        max_retries: int = 2,
        retry_backoff_s: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,  # tests inject a MockTransport
    ) -> None:
        self.base_url = base_url
        self._max_retries = max_retries
        self._backoff = retry_backoff_s
        # Connecting should be quick even when generating is slow.
        timeout = httpx.Timeout(timeout_s, connect=5.0)
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def version(self) -> str:
        return str((await self._request("GET", "/api/version"))["version"])

    async def models(self) -> set[str]:
        data = await self._request("GET", "/api/tags")
        return {m["name"] for m in data.get("models", [])}

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, payload)

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            last = attempt == self._max_retries
            try:
                response = await self._http.request(method, path, json=payload)
            except httpx.TimeoutException as exc:
                # Not retried: a generation that took the full timeout would just
                # take it again.
                raise ProviderUnavailableError(f"Ollama timed out on {path}") from exc
            except httpx.TransportError as exc:  # connection refused, DNS, reset
                if last:
                    raise ProviderUnavailableError(
                        f"cannot reach Ollama at {self.base_url} ({type(exc).__name__}); "
                        "is it running?"
                    ) from exc
            else:
                if response.status_code < 500:
                    if response.status_code >= 400:
                        # 4xx won't succeed on retry, e.g. 404 "model not found".
                        raise ProviderError(
                            f"Ollama {path} -> {response.status_code}: {response.text[:300]}"
                        )
                    return response.json()
                if last:
                    raise ProviderError(
                        f"Ollama {path} -> {response.status_code}: {response.text[:300]}"
                    )
            await asyncio.sleep(self._backoff * 2**attempt)  # 0.5 s, 1 s, ...
        raise AssertionError("unreachable")


def _usage(data: dict[str, Any]) -> Usage:
    return Usage(
        input_tokens=int(data.get("prompt_eval_count") or 0),
        output_tokens=int(data.get("eval_count") or 0),
        duration_ms=(data.get("total_duration") or 0) / 1e6,  # Ollama reports nanoseconds
    )


def l2_normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else list(vector)


class OllamaEmbedder:
    def __init__(
        self,
        client: OllamaClient,
        *,
        model: str,
        dim: int,
        batch_size: int,
        query_instruction: str,
        query_on_cpu: bool = False,
    ) -> None:
        self.model = model
        self.dim = dim
        self._client = client
        self._batch_size = batch_size
        self._query_instruction = query_instruction
        self._query_on_cpu = query_on_cpu

    async def embed(self, texts: Sequence[str], *, kind: EmbedKind) -> Embeddings:
        prepared = [self._prepare(text, kind) for text in texts]
        vectors: list[list[float]] = []
        usage = Usage()
        for start in range(0, len(prepared), self._batch_size):
            batch = prepared[start : start + self._batch_size]
            payload: dict[str, Any] = {"model": self.model, "input": batch}
            if kind is EmbedKind.QUERY and self._query_on_cpu:
                # 0 layers on GPU = CPU only: leaves the VRAM to the LLM.
                payload["options"] = {"num_gpu": 0}
            data = await self._client.post("/api/embed", payload)
            got = data.get("embeddings") or []
            if len(got) != len(batch):
                raise ProviderError(f"asked for {len(batch)} embeddings, got {len(got)}")
            for vector in got:
                if len(vector) != self.dim:
                    # The DB column is vector(EMBED_DIM); storing would fail later
                    # with a far less helpful error.
                    raise ProviderError(
                        f"{self.model} returns {len(vector)}-d vectors but EMBED_DIM={self.dim}"
                    )
            # Normalising makes cosine distance a plain dot product. Qwen3
            # already returns unit vectors, but the interface promises it for
            # every provider.
            vectors.extend(l2_normalize(v) for v in got)
            usage += _usage(data)
        return Embeddings(vectors, self.model, usage)

    def _prepare(self, text: str, kind: EmbedKind) -> str:
        if kind is EmbedKind.QUERY:
            # Qwen3-Embedding's query format; documents are embedded as-is.
            return f"Instruct: {self._query_instruction}\nQuery: {text}"
        return text


class OllamaLLM:
    def __init__(
        self,
        client: OllamaClient,
        *,
        model: str,
        num_ctx: int,
        temperature: float,
        seed: int,
        max_output_tokens: int,
    ) -> None:
        self.model = model
        self._client = client
        self._options = {"num_ctx": num_ctx, "temperature": temperature, "seed": seed}
        self._max_output_tokens = max_output_tokens

    async def generate_structured[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        think: bool = False,
        max_output_tokens: int | None = None,
    ) -> LLMResult[T]:
        conversation = [{"role": m.role, "content": m.content} for m in messages]
        options = {**self._options, "num_predict": max_output_tokens or self._max_output_tokens}
        usage = Usage()

        for attempt in (1, 2):
            data = await self._client.post(
                "/api/chat",
                {
                    "model": self.model,
                    "messages": conversation,
                    # Ollama turns the JSON schema into a grammar, so decoding
                    # can only produce schema-shaped JSON. Not every keyword is
                    # enforced (e.g. maxLength), hence the validation below.
                    "format": schema.model_json_schema(),
                    "think": think,
                    "options": options,
                    "stream": False,
                },
            )
            usage += _usage(data)
            message = data.get("message") or {}
            raw = message.get("content") or ""

            if data.get("done_reason") == "length":
                # Truncated JSON. A repair turn would hit the same limit.
                raise StructuredOutputError(
                    f"output hit the {options['num_predict']}-token limit before the JSON ended",
                    raw,
                )
            try:
                output = schema.model_validate_json(raw)
            except ValidationError as exc:
                if attempt == 2:
                    raise StructuredOutputError(
                        f"invalid {schema.__name__} after a repair attempt: {_brief(exc)}", raw
                    ) from exc
                # One repair turn: show the model its output and what was wrong.
                conversation += [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": f"That JSON was invalid: {_brief(exc)}. "
                        "Reply again with only JSON that matches the schema.",
                    },
                ]
                continue
            return LLMResult(
                output=output,
                model=self.model,
                usage=usage,
                raw=raw,
                thinking=message.get("thinking") or None,
                attempts=attempt,
            )
        raise AssertionError("unreachable")


def _brief(exc: ValidationError) -> str:
    """The first few validation errors, compactly, for repair prompts and logs."""
    parts = [
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
        for err in exc.errors()[:5]
    ]
    return "; ".join(parts)
