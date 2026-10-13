"""The review pipeline, one fixed pass (planning.md §6, naive+ B0):

    changes -> seeds -> retrieve -> pack -> generate -> validate -> record

Each stage is a plain function or object from its own module; this file only
sequences them, times them and records events.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.db.models import Repo
from core.diff import git
from core.enums import FindingStatus, ReviewSource, ReviewStatus
from core.providers import (
    LLMResult,
    Message,
    ProviderError,
    Providers,
    StructuredOutputError,
    Usage,
)
from core.review.changes import ChangeSet, collect_changes
from core.review.config import GenerationConfig, PipelineConfig
from core.review.events import END
from core.review.packing import ReviewChunk, build_chunks, fit_to_window
from core.review.prompts import build_messages, system_prompt
from core.review.recorder import Notify, ReviewRecorder
from core.review.retrieval import retrieve_for_seeds, select_seeds
from core.review.schemas import FindingOut, ReviewOut
from core.review.validate import CheckedFinding, validate
from core.review.verify import verify_findings
from core.text.tokens import estimate_tokens


class ReviewError(Exception):
    """A user-facing error (repo not indexed, bad revision, ...)."""


@dataclass
class ReviewResult:
    review_id: int
    status: ReviewStatus
    changes: ChangeSet
    chunks: list[ReviewChunk]
    left_out: list[str]
    summaries: list[str]
    findings: list[CheckedFinding]
    failed_chunks: list[tuple[int, str]]
    usage: Usage
    seconds: dict[str, float] = field(default_factory=dict)
    fallback_chunks: list[int] = field(default_factory=list)  # thinking ran out, retried without

    @property
    def posted(self) -> list[CheckedFinding]:
        return [f for f in self.findings if f.status is FindingStatus.POSTED]


ProgressFn = Callable[[str], None]

# Headroom for chat-template tokens and the estimate's error.
_WINDOW_MARGIN_TOKENS = 300
# The PR header, block tags/attributes and closing instruction around the
# diff, changed code and context (measured on real prompts: ~150-350).
_PROMPT_OVERHEAD_TOKENS = 400


class _ThinkingRanOut(Exception):
    """Thinking used up the output budget; the chunk can be retried without it."""


# The no-thinking generation a fallback uses: B0's output reserve and sampling.
_FAST = GenerationConfig()


async def _generate(
    providers: Providers, messages: list[Message], generation: GenerationConfig
) -> LLMResult[ReviewOut]:
    try:
        return await providers.llm.generate_structured(
            messages,
            ReviewOut,
            think=generation.think,
            max_output_tokens=generation.max_output_tokens,
            sampling=generation.sampling(),
        )
    except StructuredOutputError as exc:
        if generation.think and generation.think_fallback and exc.truncated:
            raise _ThinkingRanOut(str(exc)) from exc
        raise


async def run_review(
    engine: AsyncEngine,
    providers: Providers,
    *,
    repo_path: Path,
    base: str,
    head: str,
    repo_name: str,
    config: PipelineConfig,
    context_window: int,
    source: ReviewSource = ReviewSource.CLI,
    progress: ProgressFn | None = None,
    review_id: int | None = None,
    title: str | None = None,
    notify: Notify | None = None,
    final: bool = True,
) -> ReviewResult:
    """Review head against its merge-base with base. `context_window` is the
    LLM's num_ctx: every prompt is fitted to it, minus the output reserve.

    With `review_id`, the review row already exists (the API queued it) and is
    claimed instead of inserted; `source` is then the row's own. `title` is
    what the prompt calls the change (a pull request's title); by default the
    head's name.

    `notify` is rung after every event (live streaming). With `final`, the
    review's last event is `end`: nothing more will be written. The worker
    passes final=False when it still has work after the pipeline (posting
    to GitHub) and writes `end` itself."""
    say = progress or (lambda _: None)
    seconds: dict[str, float] = {}

    started = time.perf_counter()
    try:
        # git + tree-sitter work: blocking, so off the event loop.
        changes = await asyncio.to_thread(collect_changes, repo_path, base, head)
        commits = await asyncio.to_thread(
            git.commit_subjects, repo_path, changes.base_sha, changes.head_sha
        )
    except git.GitError as exc:
        raise ReviewError(str(exc)) from exc
    seconds["changes"] = time.perf_counter() - started

    async with engine.connect() as conn:
        repo_id = await conn.scalar(select(Repo.id).where(Repo.full_name == repo_name))
        await conn.commit()
        if repo_id is None:
            raise ReviewError(
                f"no indexed repo named {repo_name!r}: run core.index (and core.embed) first"
            )

        recorder = ReviewRecorder(conn, notify)
        review_id = await recorder.start(
            repo_id=repo_id,
            source=source,
            base_sha=changes.base_sha,
            head_sha=changes.head_sha,
            model=providers.llm.model,
            config=config,
            review_id=review_id,
            diff=changes.diff,
            seeds=changes.seeds,
        )
        usage = Usage()
        try:
            seeds = select_seeds(changes.seeds, config.retrieval.max_seeds)
            await recorder.event("stage", stage="seeds", files=len(changes.files), seeds=len(seeds))
            say(f"{len(changes.files)} files changed, {len(seeds)} seeds")

            started = time.perf_counter()
            candidates = await retrieve_for_seeds(
                conn,
                providers.embedder,
                repo_id=repo_id,
                seeds=seeds,
                config=config.retrieval,
            )
            await conn.commit()
            seconds["retrieve"] = time.perf_counter() - started

            # Budgets follow the model's window: what's left after the output
            # reserve, a safety margin, and the fixed parts of every prompt.
            system = system_prompt(check_callers=config.generation.check_callers)
            fixed = estimate_tokens(system) + _PROMPT_OVERHEAD_TOKENS
            available = (
                context_window - config.generation.max_output_tokens - _WINDOW_MARGIN_TOKENS - fixed
            )
            packing = config.packing.scaled_to(max(available, 1))
            chunks, left_out = build_chunks(changes, seeds, candidates, packing)
            await recorder.event(
                "stage",
                stage="packed",
                chunks=len(chunks),
                left_out=left_out,
                budgets={
                    "diff": packing.diff_tokens,
                    "changed": packing.changed_node_tokens,
                    "context": packing.context_tokens,
                },
            )
            say(
                f"{len(chunks)} review chunk(s)"
                + (f", {len(left_out)} files left out" if left_out else "")
            )

            started = time.perf_counter()
            raw: list[tuple[ReviewChunk, list[FindingOut]]] = []
            summaries: list[str] = []
            failed: list[tuple[int, str]] = []
            fallbacks: list[int] = []  # chunks reviewed again without thinking
            if title is None:
                title = head if not head.startswith("HEAD") else "local changes"
            prompt_budget = (
                context_window - config.generation.max_output_tokens - _WINDOW_MARGIN_TOKENS
            )

            def render(c: ReviewChunk) -> list[Message]:
                return build_messages(
                    c,
                    title=title,
                    commits=commits,
                    max_findings=config.generation.max_findings_per_chunk,
                    check_callers=config.generation.check_callers,
                )

            def measure(c: ReviewChunk) -> int:
                return sum(estimate_tokens(m.content) for m in render(c))

            for position, packed in enumerate(chunks):
                fitted = fit_to_window(packed, measure, prompt_budget)
                if fitted is None:
                    error = f"the diff alone exceeds the {context_window}-token context window"
                    failed.append((packed.index, error))
                    await recorder.event("chunk_failed", chunk=packed.index, error=error)
                    continue
                chunk = chunks[position] = fitted  # the trace records what was actually sent
                await recorder.event(
                    "stage",
                    stage="generating",
                    chunk=chunk.index,
                    of=len(chunks),
                    prompt_tokens=measure(chunk),
                )
                say(f"generating chunk {chunk.index + 1}/{len(chunks)} ({', '.join(chunk.paths)})")
                messages = render(chunk)
                try:
                    result = await _generate(providers, messages, config.generation)
                except _ThinkingRanOut as exc:
                    # The chunk is reviewed again without thinking (D86).
                    fallbacks.append(chunk.index)
                    await recorder.event("think_fallback", chunk=chunk.index, error=str(exc))
                    try:
                        result = await _generate(providers, messages, _FAST)
                    except ProviderError as retry_exc:
                        failed.append((chunk.index, str(retry_exc)))
                        await recorder.event(
                            "chunk_failed", chunk=chunk.index, error=str(retry_exc)
                        )
                        continue
                except ProviderError as exc:
                    # One failed chunk shouldn't sink the whole review.
                    failed.append((chunk.index, str(exc)))
                    await recorder.event("chunk_failed", chunk=chunk.index, error=str(exc))
                    continue
                usage += result.usage
                await recorder.event(
                    "chunk_done",
                    chunk=chunk.index,
                    estimated_prompt_tokens=measure(chunk),
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    llm_ms=round(result.usage.duration_ms),
                    findings=len(result.output.findings),
                )
                summaries.append(result.output.summary)
                raw.append(
                    (chunk, result.output.findings[: config.generation.max_findings_per_chunk])
                )
            seconds["generate"] = time.perf_counter() - started

            checked = validate(raw, config.validation)
            if config.verification.enabled:
                checked, spent = await verify_findings(
                    conn,
                    providers.llm,
                    repo_id=repo_id,
                    chunks=chunks,
                    checked=checked,
                    config=config.verification,
                    event=recorder.event,
                )
                usage += spent
            await recorder.context(chunks)
            await recorder.findings(checked)
            status = (
                ReviewStatus.FAILED
                if chunks and len(failed) == len(chunks)
                else ReviewStatus.COMPLETED
            )
            posted = sum(1 for c in checked if c.status is FindingStatus.POSTED)
            await recorder.event(
                "done", status=status.value, posted=posted, dropped=len(checked) - posted
            )
            await recorder.finish(
                status, usage, error=failed[0][1] if status is ReviewStatus.FAILED else None
            )
            if final:
                await recorder.event(END, status=status.value)
        except Exception as exc:
            await conn.rollback()
            await recorder.finish(ReviewStatus.FAILED, usage, error=repr(exc))
            if final:
                await recorder.event(END, status=ReviewStatus.FAILED.value)
            raise

    return ReviewResult(
        review_id=review_id,
        status=status,
        changes=changes,
        chunks=chunks,
        left_out=left_out,
        summaries=summaries,
        findings=checked,
        failed_chunks=failed,
        fallback_chunks=fallbacks,
        usage=usage,
        seconds=seconds,
    )
