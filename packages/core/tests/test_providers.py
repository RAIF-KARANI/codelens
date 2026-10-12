"""Provider contract tests. Ollama is simulated with httpx.MockTransport, so
these run offline and fast; `python -m core.providers` checks the real thing."""

import asyncio
import json
import math
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from core.providers import (
    EmbedKind,
    Message,
    ProviderError,
    ProviderUnavailableError,
    StructuredOutputError,
)
from core.providers.fake import FakeEmbedder, FakeLLM, FakeReranker
from core.providers.ollama import OllamaClient, OllamaEmbedder, OllamaLLM

Handler = Callable[[httpx.Request], httpx.Response]


class Finding(BaseModel):
    title: str
    severity: str


class Review(BaseModel):
    findings: list[Finding]


def client(handler: Handler) -> OllamaClient:
    return OllamaClient(
        "http://ollama.test",
        timeout_s=5,
        retry_backoff_s=0,  # no sleeping in tests
        transport=httpx.MockTransport(handler),
    )


def body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


def run[T](coro: Any) -> T:
    return asyncio.run(coro)


# ---------------------------------------------------------------- embeddings


def test_embedder_batches_prefixes_queries_and_normalizes() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = body(request)
        requests.append(payload)
        # Deliberately unnormalised vectors: [3, 4] has length 5.
        vectors = [[3.0, 4.0] for _ in payload["input"]]
        return httpx.Response(200, json={"embeddings": vectors, "prompt_eval_count": 7})

    embedder = OllamaEmbedder(
        client(handler), model="emb", dim=2, batch_size=2, query_instruction="Find code"
    )
    docs = run(embedder.embed(["a", "b", "c"], kind=EmbedKind.DOCUMENT))
    query = run(embedder.embed(["q"], kind=EmbedKind.QUERY))

    assert [r["input"] for r in requests] == [
        ["a", "b"],  # batch_size=2 -> two requests for three texts
        ["c"],
        ["Instruct: Find code\nQuery: q"],  # only queries get the instruction
    ]
    assert docs.vectors == [[0.6, 0.8]] * 3  # L2-normalised
    assert docs.usage.input_tokens == 14
    assert query.vectors == [[0.6, 0.8]]
    assert "options" not in requests[0]  # GPU by default


def test_embedder_can_run_queries_on_cpu_and_documents_on_gpu() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(body(request))
        return httpx.Response(200, json={"embeddings": [[1.0]]})

    embedder = OllamaEmbedder(
        client(handler), model="emb", dim=1, batch_size=8, query_instruction="", query_on_cpu=True
    )
    run(embedder.embed(["x"], kind=EmbedKind.QUERY))
    run(embedder.embed(["x"], kind=EmbedKind.DOCUMENT))

    assert seen[0]["options"] == {"num_gpu": 0}  # query: CPU
    assert "options" not in seen[1]  # document (bulk indexing): GPU


def test_embedder_rejects_wrong_dimension() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[1.0, 0.0, 0.0]]})

    embedder = OllamaEmbedder(
        client(handler), model="emb", dim=1024, batch_size=8, query_instruction=""
    )

    with pytest.raises(ProviderError, match="3-d vectors but EMBED_DIM=1024"):
        run(embedder.embed(["x"], kind=EmbedKind.DOCUMENT))


# ---------------------------------------------------------------- client errors


def test_client_retries_server_errors_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, text="loading model")
        return httpx.Response(200, json={"version": "1.0"})

    assert run(client(handler).version()) == "1.0"
    assert calls == 3  # two retries


def test_client_does_not_retry_client_errors() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, text='{"error":"model \\"nope\\" not found"}')

    with pytest.raises(ProviderError, match="404"):
        run(client(handler).post("/api/chat", {}))
    assert calls == 1


def test_client_reports_unreachable_ollama() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ProviderUnavailableError, match="is it running"):
        run(client(handler).version())


# ---------------------------------------------------------------- structured LLM output


def llm(handler: Handler) -> OllamaLLM:
    return OllamaLLM(
        client(handler), model="m", num_ctx=4096, temperature=0.1, seed=7, max_output_tokens=500
    )


def chat_response(content: str, *, done_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "message": {"role": "assistant", "content": content},
            "done_reason": done_reason,
            "prompt_eval_count": 100,
            "eval_count": 20,
            "total_duration": 2_000_000_000,
        },
    )


GOOD = '{"findings": [{"title": "Division by zero", "severity": "critical"}]}'


def test_llm_sends_schema_and_options_and_parses_output() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(body(request))
        return chat_response(GOOD)

    result = run(
        llm(handler).generate_structured(
            [Message("system", "s"), Message("user", "u")], Review, think=True
        )
    )

    assert result.output == Review(
        findings=[Finding(title="Division by zero", severity="critical")]
    )
    assert (result.usage.input_tokens, result.usage.output_tokens) == (100, 20)
    assert result.usage.duration_ms == 2000
    assert result.attempts == 1
    sent = seen[0]
    assert sent["format"] == Review.model_json_schema()
    assert sent["think"] is True
    assert sent["stream"] is False
    assert sent["options"] == {"num_ctx": 4096, "temperature": 0.1, "seed": 7, "num_predict": 500}
    assert sent["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]


def test_llm_repairs_invalid_output_once() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(body(request))
        return chat_response('{"findings": [{"title": "x"}]}' if len(seen) == 1 else GOOD)

    result = run(llm(handler).generate_structured([Message("user", "u")], Review))

    assert result.attempts == 2
    assert result.usage.input_tokens == 200  # both calls counted
    repair = seen[1]["messages"]
    assert repair[1] == {"role": "assistant", "content": '{"findings": [{"title": "x"}]}'}
    assert "findings.0.severity: Field required" in repair[2]["content"]


def test_llm_gives_up_after_one_repair() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_response("not json at all")

    with pytest.raises(StructuredOutputError, match="after a repair attempt") as caught:
        run(llm(handler).generate_structured([Message("user", "u")], Review))
    assert caught.value.raw == "not json at all"


def test_llm_does_not_repair_truncated_output() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return chat_response('{"findings": [{"title": "Divi', done_reason="length")

    with pytest.raises(StructuredOutputError, match="500-token limit") as caught:
        run(llm(handler).generate_structured([Message("user", "u")], Review))
    assert calls == 1
    # The error says where the tokens went, so a truncation can be diagnosed.
    assert 'thinking 0 chars, JSON 29 chars ending \'{"findings"' in str(caught.value)


# ---------------------------------------------------------------- fakes


def test_fake_embedder_is_deterministic_normalized_and_lexical() -> None:
    embedder = FakeEmbedder(dim=256)
    texts = ["def get_user_by_id(user_id)", "getUserById(userId)", "render the header html"]

    first = run(embedder.embed(texts, kind=EmbedKind.DOCUMENT)).vectors
    again = run(embedder.embed(texts, kind=EmbedKind.QUERY)).vectors

    assert first == again
    assert all(math.isclose(sum(x * x for x in v), 1.0) for v in first)

    def cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    # Same identifiers in different casing are close; unrelated text is not.
    assert cos(first[0], first[1]) > cos(first[0], first[2])


def test_fake_llm_validates_and_records_calls() -> None:
    fake = FakeLLM(responder=lambda messages, schema: {"findings": []})

    result = run(fake.generate_structured([Message("user", "hi")], Review, think=True))

    assert result.output == Review(findings=[])
    assert fake.calls[0].schema is Review
    assert fake.calls[0].think is True


def test_fake_reranker_scores_by_overlap() -> None:
    scores = run(FakeReranker().rerank("user id lookup", ["get user by id", "render header"]))

    assert scores[0] > scores[1] == 0.0


def test_embedder_sends_its_context_size_for_queries_and_documents() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(body(request))
        return httpx.Response(200, json={"embeddings": [[1.0]]})

    embedder = OllamaEmbedder(
        client(handler), model="emb", dim=1, batch_size=8, query_instruction="", num_ctx=2048
    )
    run(embedder.embed(["x"], kind=EmbedKind.QUERY))
    run(embedder.embed(["x"], kind=EmbedKind.DOCUMENT))

    # Same options both ways: a difference would make Ollama reload the model.
    assert seen[0]["options"] == seen[1]["options"] == {"num_ctx": 2048}


def test_sampling_overrides_the_provider_defaults_for_one_call() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(body(request))
        return chat_response(GOOD)

    provider = llm(handler)
    sampling = {"temperature": 0.6, "top_p": 0.95, "top_k": 20}
    run(provider.generate_structured([Message("user", "u")], Review, sampling=sampling))
    run(provider.generate_structured([Message("user", "u")], Review))

    with_override, default = (s["options"] for s in seen)
    assert with_override == {**default, **sampling}  # seed and num_ctx kept
    assert default["temperature"] == 0.1 and "top_p" not in default
