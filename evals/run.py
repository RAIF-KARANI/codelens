"""Run the review pipeline over eval cases and record every result.

    python -m evals.run --config configs/pipeline/b0.yaml [--split dev|test|all]
                        [--operator OP ...] [--case ID ...] [--limit N]
                        [--name NAME] [--resume]

For each source the selected cases use: check out the pinned commit, then index
and embed it as `eval/<source>` (incremental, so a no-op after the first run).
Each case's patch becomes a commit on top of the pinned one, built with git
plumbing: no checkout, and a fixed author/date, so the same case is always the
same commit. run_review reviews it like any branch (source=eval).

A counterfactual case is based on its prep commit instead, and with retrieval
on, that commit is indexed (in a worktree, as `eval-cf/<source>`) before the
review: the context must show the callers as prepared.

Results are appended per case to evals/results/<name>/cases.jsonl, so an
interrupted run continues with --resume. A scorecard is written at the end.
"""

import argparse
import asyncio
import hashlib
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import Settings, get_settings
from core.db import create_engine
from core.diff.git import GitError
from core.embedding import embed_repository
from core.enums import ReviewSource
from core.indexing import index_repository
from core.providers import ProviderError, Providers, open_providers
from core.review.config import PipelineConfig
from core.review.pipeline import ReviewError, ReviewResult, run_review
from evals.cases import Case, load_cases
from evals.gitops import commit_patch
from evals.metrics import score_case
from evals.records import (
    CaseRecord,
    Prediction,
    Retrieved,
    RunInfo,
    append_case,
    load_run,
    write_run,
)
from evals.report import write_scorecard
from evals.sources import DEFAULT_CHECKOUTS, DEFAULT_SOURCES, Source, checkout, load_sources

DEFAULT_DATASET = Path("evals/datasets")
DEFAULT_RESULTS = Path("evals/results")
_MAX_CONSECUTIVE_ERRORS = 3  # e.g. Ollama is down: stop instead of failing 97 cases


def _say(line: str) -> None:
    print(line, flush=True)  # live progress even when stdout goes to a log file


@dataclass(frozen=True, slots=True)
class Prepared:
    """A source ready to review against: checked out, indexed, embedded."""

    name: str
    root: Path
    repo_name: str
    worktree: Path  # where counterfactual bases are checked out for indexing


Selected = list[tuple[Case, str]]


def select_cases(
    dataset: Path,
    *,
    split: str = "all",
    operators: Sequence[str] = (),
    ids: Sequence[str] = (),
    limit: int | None = None,
) -> Selected:
    chosen = [
        (case, patch)
        for case, patch in load_cases(dataset)
        if (split == "all" or case.split == split)
        and (not operators or case.operator in operators)
        and (not ids or case.id in ids)
    ]
    return chosen[:limit] if limit is not None else chosen


def dataset_hash(cases: Selected) -> str:
    digest = hashlib.sha256()
    for case, patch in cases:
        digest.update(case.model_dump_json().encode())
        digest.update((case.prep or "").encode())
        digest.update(patch.encode())
    return digest.hexdigest()


def case_commits(repo: Path, case: Case, patch: str) -> tuple[str, str]:
    """(base, head) commits for a case: base is the pinned commit, or for a
    counterfactual the pinned commit plus its prep.

    The head's message names only the file. It reaches the prompt's commit
    list, and an id such as `x.raise_to_none.1a2b` would tell the model which
    bug to look for (it did, until D77)."""
    base = case.sha
    if case.prep is not None:
        base = commit_patch(repo, case.sha, case.prep, "Update call sites")
    return base, commit_patch(repo, base, patch, f"Update {case.file}")


async def prepare_source(
    engine: AsyncEngine,
    providers: Providers,
    source: Source,
    checkouts: Path,
    *,
    embed_max_tokens: int,
    say: Callable[[str], None] = _say,
) -> Prepared:
    root = checkout(source, checkouts)
    name = f"eval/{source.name}"
    started = time.perf_counter()
    await index_repository(engine, root, name=name)
    report = await embed_repository(engine, providers.embedder, name, max_tokens=embed_max_tokens)
    say(
        f"{name} @ {source.sha[:10]}: indexed, {report.embedded} embedded "
        f"({report.up_to_date} up to date) in {time.perf_counter() - started:.0f}s"
    )
    return Prepared(source.name, root, name, checkouts.parent / "worktrees" / source.name)


async def index_at(
    engine: AsyncEngine,
    providers: Providers,
    source: Prepared,
    commit: str,
    *,
    embed_max_tokens: int,
) -> str:
    """Index `commit` (a counterfactual's prep commit) as `eval-cf/<source>`,
    from a worktree, so retrieval sees the callers as prepared. Incremental:
    only what differs from the previous counterfactual is re-indexed."""
    if (source.worktree / ".git").exists():
        _git(source.worktree, "checkout", "--quiet", "--force", "--detach", commit)
    else:
        _git(source.root, "worktree", "prune")
        _git(source.root, "worktree", "add", "--force", "--detach", str(source.worktree), commit)
    name = f"eval-cf/{source.name}"
    await index_repository(engine, source.worktree, name=name)
    await embed_repository(engine, providers.embedder, name, max_tokens=embed_max_tokens)
    return name


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def to_record(case: Case, result: ReviewResult, seconds: float) -> CaseRecord:
    findings = [
        Prediction(
            file=c.finding.file_path,
            line_start=c.line_start,
            line_end=c.line_end,
            category=c.finding.category,
            severity=c.finding.severity,
            confidence=c.finding.confidence,
            title=c.finding.title,
            status=c.status,
            drop_reason=c.drop_reason,
        )
        for c in result.findings
    ]
    # One ranking for the whole review: every chunk's candidates, best
    # similarity first (the order retrieval itself uses, merge_candidates).
    best: dict[int, Retrieved] = {}
    for chunk in result.chunks:
        for item in chunk.trace:
            c = item.candidate
            seen = best.get(c.node_id)
            best[c.node_id] = Retrieved(
                qualified_name=c.qualified_name,
                ordinal=c.ordinal,
                similarity=max(c.similarity, seen.similarity if seen else -1.0),
                included=item.included or (seen.included if seen else False),
            )
    ranked = sorted(best.items(), key=lambda kv: (-kv[1].similarity, kv[0]))
    return CaseRecord(
        **_case_fields(case),
        status="completed" if result.status.value == "completed" else "failed",
        review_id=result.review_id,
        seconds=seconds,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        chunks=len(result.chunks),
        failed_chunks=len(result.failed_chunks),
        findings=findings,
        retrieved=[r for _, r in ranked],
    )


def _case_fields(case: Case) -> dict[str, object]:
    return {
        "case_id": case.id,
        "kind": case.kind,
        "split": case.split,
        "operator": case.operator,
        "source": case.source,
        "expected": case.expected,
        "oracle": case.oracle,
    }


async def run_cases(
    engine: AsyncEngine,
    providers: Providers,
    cases: Selected,
    prepared: dict[str, Prepared],
    *,
    config: PipelineConfig,
    context_window: int,
    out: Path,
    embed_max_tokens: int = 1024,
    say: Callable[[str], None] = _say,
) -> None:
    """Review each case and append its record. Cases already in `out` are skipped."""
    done = {r.case_id for r in load_run(out)[1]}
    todo = [(c, p) for c, p in cases if c.id not in done]
    errors_in_a_row = 0
    for n, (case, patch) in enumerate(todo, 1):
        source = prepared[case.source]
        started = time.perf_counter()
        try:
            base, head = await asyncio.to_thread(case_commits, source.root, case, patch)
            repo_name = source.repo_name
            if case.prep is not None and config.retrieval.enabled:
                repo_name = await index_at(
                    engine, providers, source, base, embed_max_tokens=embed_max_tokens
                )
            started = time.perf_counter()  # latency is the review's, not the indexing's
            result = await run_review(
                engine,
                providers,
                repo_path=source.root,
                base=base,
                head=head,
                repo_name=repo_name,
                config=config,
                context_window=context_window,
                source=ReviewSource.EVAL,
            )
            record = to_record(case, result, time.perf_counter() - started)
            errors_in_a_row = 0
        except (ReviewError, ProviderError, GitError, subprocess.CalledProcessError) as exc:
            record = CaseRecord(
                **_case_fields(case),
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                seconds=time.perf_counter() - started,
            )
            errors_in_a_row += 1
        append_case(out, record)
        score = score_case(record)
        verdict = (
            f"tp {score.tp} fp {score.fp}" if score.kind == "seeded" else f"posted {score.posted}"
        )
        say(
            f"[{len(done) + n}/{len(cases)}] {case.id:<44} {record.status:<9} "
            f"{record.seconds:5.1f}s  {verdict}" + (f"  {record.error}" if record.error else "")
        )
        if errors_in_a_row >= _MAX_CONSECUTIVE_ERRORS:
            raise SystemExit(f"stopping: {errors_in_a_row} errors in a row ({record.error})")


async def model_digests(settings: Settings) -> dict[str, str]:
    """Ollama's digest per model in use, so a result names the exact weights."""
    wanted = {
        settings.llm_model: settings.llm_provider,
        settings.embed_model: settings.embed_provider,
    }
    digests = {m: "fake" for m, provider in wanted.items() if provider == "fake"}
    if len(digests) < len(wanted):
        async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=10) as client:
            tags = (await client.get("/api/tags")).json().get("models", [])
        by_name = {t["name"]: t.get("digest", "?") for t in tags}
        for model in wanted.keys() - digests.keys():
            digests[model] = by_name.get(model) or by_name.get(f"{model}:latest", "unknown")
    return digests


def _codelens_commit() -> str | None:
    done = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return done.stdout.strip() or None


async def _main(args: argparse.Namespace) -> Path:
    settings = get_settings()
    config = PipelineConfig.from_yaml(args.config)
    cases = select_cases(
        args.dataset, split=args.split, operators=args.operator, ids=args.case, limit=args.limit
    )
    if not cases:
        raise SystemExit("no cases match the filters")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    out = args.results / (args.name or f"{config.name}-{args.split}-{stamp}")
    info = RunInfo(
        name=out.name,
        config=config.model_dump(mode="json"),
        llm_model=settings.llm_model,
        embed_model=settings.embed_model,
        model_digests=await model_digests(settings),
        context_window=settings.llm_num_ctx,
        temperature=settings.llm_temperature,
        seed=settings.llm_seed,
        codelens_commit=_codelens_commit(),
        dataset_hash=dataset_hash(cases),
        cases=len(cases),
        filters={
            "split": args.split,
            "operator": args.operator,
            "case": args.case,
            "limit": args.limit,
        },
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    if (out / "run.json").exists():
        if not args.resume:
            raise SystemExit(f"{out} exists: pass --resume to continue it, or another --name")
        previous, _ = load_run(out)
        same = ("config", "llm_model", "model_digests", "context_window", "dataset_hash")
        if any(getattr(previous, f) != getattr(info, f) for f in same):
            raise SystemExit(f"{out} was run with different settings or cases; use another --name")
    else:
        write_run(out, info)

    sources = {s.name: s for s in load_sources(args.sources)}
    engine = create_engine(settings.database_url, pooled=False)
    try:
        async with open_providers(settings) as providers:
            prepared = {
                name: await prepare_source(
                    engine,
                    providers,
                    sources[name],
                    args.checkouts,
                    embed_max_tokens=settings.embed_max_tokens,
                )
                for name in sorted({c.source for c, _ in cases})
            }
            await run_cases(
                engine,
                providers,
                cases,
                prepared,
                config=config,
                context_window=settings.llm_num_ctx,
                out=out,
                embed_max_tokens=settings.embed_max_tokens,
            )
    finally:
        await engine.dispose()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, required=True, help="pipeline config YAML")
    parser.add_argument("--split", choices=("dev", "test", "all"), default="dev")
    parser.add_argument("--operator", action="append", default=[], help="only this operator")
    parser.add_argument("--case", action="append", default=[], help="only this case id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--name", help="results directory name (default <config>-<split>-<time>)")
    parser.add_argument("--resume", action="store_true", help="continue an interrupted run")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--checkouts", type=Path, default=DEFAULT_CHECKOUTS)
    args = parser.parse_args()

    out = asyncio.run(_main(args))
    card = write_scorecard(out)
    print(f"\nscorecard: {card}")
    print(card.read_text(encoding="utf-8"))
    sys.exit(0)


if __name__ == "__main__":
    main()
