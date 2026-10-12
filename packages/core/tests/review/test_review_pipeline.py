"""The whole review pipeline against a real git repo and Postgres, with the
deterministic FakeEmbedder and a scripted FakeLLM (no model needed)."""

import asyncio
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import text

from core.config import get_settings
from core.db import create_engine
from core.embedding import embed_repository
from core.enums import ReviewStatus
from core.indexing import index_repository
from core.providers import Message, Providers, StructuredOutputError
from core.providers.fake import FakeEmbedder, FakeLLM
from core.review.config import (
    GenerationConfig,
    PipelineConfig,
    RetrievalConfig,
    VerificationConfig,
)
from core.review.pipeline import ReviewError, ReviewResult, run_review
from core.review.schemas import ReviewOut
from core.review.verify import VerifyOut

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parents[1] / "fixtures" / "repos" / "py_shop"
NAME = "local/shop"


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )  # fmt: skip


@pytest.fixture
def shop(tmp_path: Path, migrated_database_url: str) -> Path:
    """py_shop as a git repo: base indexed + embedded on main, a buggy change on a branch."""
    repo = Path(shutil.copytree(FIXTURE, tmp_path / "shop"))
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")

    async def index_and_embed() -> None:
        engine = create_engine(migrated_database_url, pooled=False)
        try:
            await index_repository(engine, repo, name=NAME)
            await embed_repository(
                engine, FakeEmbedder(get_settings().embed_dim), NAME, max_tokens=1024
            )
        finally:
            await engine.dispose()

    asyncio.run(index_and_embed())

    git(repo, "checkout", "-q", "-b", "reject-bad-discounts")
    pricing = repo / "shop" / "pricing.py"
    pricing.write_text(
        pricing.read_text().replace(
            '    """Return amount after a percentage discount."""\n',
            '    """Return amount after a percentage discount."""\n'
            "    if percent > 100:\n"
            "        return None\n",
        ),
        newline="\n",
    )
    git(repo, "commit", "-q", "-am", "Reject discounts above 100%")
    return repo


def scripted(messages: Sequence[Message], schema: type[BaseModel]) -> dict[str, Any]:
    """Cite the first context block shown, if any; mix valid and invalid findings."""
    shown = re.findall(r'<context id="(N\d+)"', messages[-1].content)
    good = {
        "file_path": "shop/pricing.py",
        "line_start": 8,
        "line_end": 9,
        "severity": "critical",
        "category": "api-contract",
        "title": "apply_discount can now return None",
        "explanation": "with_tax multiplies the result.",
        "evidence": shown[:1],
        "confidence": 0.9,
    }
    return {
        "summary": "Adds a guard that returns None.",
        "findings": [
            good,
            {**good, "confidence": 0.8},  # duplicate
            {**good, "line_start": 12, "line_end": 12, "title": "with_tax untouched"},
        ],
    }


def review(
    url: str, repo: Path, llm: FakeLLM, config: PipelineConfig | None = None, name: str = NAME
) -> ReviewResult:
    async def go() -> ReviewResult:
        engine = create_engine(url, pooled=False)
        try:
            providers = Providers(FakeEmbedder(get_settings().embed_dim), llm)
            return await run_review(
                engine,
                providers,
                repo_path=repo,
                base="main",
                head="reject-bad-discounts",
                repo_name=name,
                config=config or PipelineConfig(),
                context_window=8192,
            )
        finally:
            await engine.dispose()

    return asyncio.run(go())


def query(url: str, sql: str) -> list[Any]:
    async def go() -> list[Any]:
        engine = create_engine(url, pooled=False)
        try:
            async with engine.connect() as conn:
                return list(await conn.execute(text(sql)))
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_b0_review_end_to_end(shop: Path, migrated_database_url: str) -> None:
    llm = FakeLLM(responder=scripted)

    result = review(migrated_database_url, shop, llm)

    assert result.status is ReviewStatus.COMPLETED
    assert [(c.status.value, c.drop_reason) for c in result.findings] == [
        ("posted", None),
        ("dropped", "duplicate"),
        ("dropped", "not_on_changed_lines"),
    ]
    [prompt] = [call.messages[-1].content for call in llm.calls]
    assert '<changed id="C1" name="shop.pricing.apply_discount"' in prompt
    assert "    8 +     if percent > 100:" in prompt  # line-numbered diff

    [row] = query(
        migrated_database_url,
        "SELECT status, source, pipeline_config->>'name' AS cfg, model FROM reviews",
    )
    assert (row.status, row.source, row.cfg, row.model) == ("completed", "cli", "b0", "fake-llm")
    stored = query(
        migrated_database_url,
        "SELECT status, drop_reason, evidence_node_ids FROM findings ORDER BY id",
    )
    assert [(r.status, r.drop_reason) for r in stored] == [
        ("posted", None),
        ("dropped", "duplicate"),
        ("dropped", "not_on_changed_lines"),
    ]
    assert len(stored[0].evidence_node_ids) == 1  # the cited context node
    items = query(migrated_database_url, "SELECT included, stage_scores FROM review_context_items")
    assert items and any(i.included for i in items)
    assert all("vector_similarity" in i.stage_scores for i in items)
    events = query(migrated_database_url, "SELECT seq, type FROM review_events ORDER BY seq")
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert events[-1].type == "done"


def test_a0_diff_only_retrieves_nothing(shop: Path, migrated_database_url: str) -> None:
    llm = FakeLLM(responder=lambda m, s: {"summary": "ok", "findings": []})
    config = PipelineConfig(name="a0", retrieval=RetrievalConfig(enabled=False))

    result = review(migrated_database_url, shop, llm, config)

    assert result.status is ReviewStatus.COMPLETED
    assert "No other code from the repository was retrieved" in llm.calls[0].messages[-1].content
    assert query(migrated_database_url, "SELECT count(*) AS n FROM review_context_items")[0].n == 0


def test_llm_failure_marks_the_review_failed(shop: Path, migrated_database_url: str) -> None:
    def broken(messages: Sequence[Message], schema: type[BaseModel]) -> dict[str, Any]:
        raise StructuredOutputError("invalid ReviewOut after a repair attempt", raw="{")

    result = review(migrated_database_url, shop, FakeLLM(responder=broken))

    assert result.status is ReviewStatus.FAILED
    assert result.failed_chunks and "repair attempt" in result.failed_chunks[0][1]
    [row] = query(migrated_database_url, "SELECT status, error FROM reviews")
    assert row.status == "failed" and "repair attempt" in row.error
    types = [
        r.type for r in query(migrated_database_url, "SELECT type FROM review_events ORDER BY seq")
    ]
    assert "chunk_failed" in types


def test_unindexed_repo_is_a_clear_error(shop: Path, migrated_database_url: str) -> None:
    with pytest.raises(ReviewError, match=r"run core\.index"):
        review(migrated_database_url, shop, FakeLLM(), name="local/never-indexed")


def test_caller_aware_context_tags_callers_and_adds_the_rule(
    shop: Path, migrated_database_url: str
) -> None:
    llm = FakeLLM(responder=lambda m, s: {"summary": "ok", "findings": []})
    config = PipelineConfig(
        name="callers",
        retrieval=RetrievalConfig(tag_relations=True),
        generation=GenerationConfig(check_callers=True),
    )

    review(migrated_database_url, shop, llm, config)

    [call] = llm.calls
    system, prompt = call.messages[0].content, call.messages[-1].content
    assert 'relation="calls the changed code"' in system
    tagged = re.findall(r'<context id="N\d+" name="([^"]+)"[^>]*relation="([^"]+)"', prompt)
    # Every tag is true (only the graph decides it), and the direct callers
    # of apply_discount that retrieval found are marked as callers.
    callers = {"shop.pricing.with_tax", "shop.plugins.holiday_price", "shop.services.checkout"}
    assert tagged, "retrieval found no caller to tag"
    for name, relation in tagged:
        assert relation == "calls the changed code" and name in callers, (name, relation)


@pytest.mark.parametrize("fallback", [True, False])
def test_thinking_that_runs_out_falls_back_to_a_plain_review(
    shop: Path, migrated_database_url: str, fallback: bool
) -> None:
    def loops_when_thinking(messages: Sequence[Message], schema: type[BaseModel]) -> Any:
        if len(llm.calls) == 1:  # the thinking call: its chain of thought never ends
            raise StructuredOutputError("output hit the 3500-token limit", raw="", truncated=True)
        return {"summary": "ok", "findings": []}

    llm = FakeLLM(responder=loops_when_thinking)
    config = PipelineConfig(
        name="think",
        generation=GenerationConfig(
            think=True, max_output_tokens=3500, temperature=0.6, think_fallback=fallback
        ),
    )

    result = review(migrated_database_url, shop, llm, config)

    if not fallback:  # without the fallback, the chunk (and so the review) fails
        assert result.status is ReviewStatus.FAILED and len(llm.calls) == 1
        return
    assert result.status is ReviewStatus.COMPLETED and result.fallback_chunks == [0]
    thinking, plain = llm.calls
    assert (thinking.think, thinking.sampling) == (True, {"temperature": 0.6})
    assert (plain.think, plain.sampling) == (False, {})  # B0's generation settings
    types = [r.type for r in query(migrated_database_url, "SELECT type FROM review_events")]
    assert "think_fallback" in types and "chunk_failed" not in types


@pytest.mark.parametrize("still_a_problem", [False, True])
def test_verification_checks_findings_against_the_real_callers(
    shop: Path, migrated_database_url: str, still_a_problem: bool
) -> None:
    def respond(messages: Sequence[Message], schema: type[BaseModel]) -> dict[str, Any]:
        if schema is VerifyOut:
            return {"still_a_problem": still_a_problem, "reason": "see with_tax"}
        return scripted(messages, schema)

    llm = FakeLLM(responder=respond)
    config = PipelineConfig(name="verify", verification=VerificationConfig(enabled=True))

    result = review(migrated_database_url, shop, llm, config)

    review_call, verify_call = llm.calls  # one review, one check of the one posted finding
    assert review_call.schema is ReviewOut
    assert verify_call.schema is VerifyOut
    prompt = verify_call.messages[-1].content
    # The callers come from the graph, every one of them, tests excluded.
    assert '<caller name="shop.pricing.with_tax"' in prompt
    assert '<caller name="shop.services.checkout"' in prompt
    assert "tests/" not in "".join(re.findall(r'path="([^"]+)"', prompt))
    [first, *_] = result.findings
    if still_a_problem:
        assert (first.status.value, first.drop_reason) == ("posted", None)
    else:
        assert (first.status.value, first.drop_reason) == ("dropped", "verified_safe")
    stored = query(migrated_database_url, "SELECT drop_reason FROM findings ORDER BY id")
    assert stored[0].drop_reason == (None if still_a_problem else "verified_safe")
