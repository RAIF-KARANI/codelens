"""Review a local branch end to end and print the findings.

    python -m core.review <repo_path> --base main [--head HEAD]
                          [--repo-name local/x] [--config configs/pipeline/b0.yaml]

The repo must already be indexed (core.index) and, for retrieved context,
embedded (core.embed). Everything is recorded: reviews, findings (posted and
dropped, with reasons), review_context_items, review_events.
"""

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

from core.config import get_settings
from core.db import create_engine
from core.enums import FindingStatus
from core.providers import ProviderError, open_providers
from core.review.config import PipelineConfig
from core.review.pipeline import ReviewError, ReviewResult, run_review

DEFAULT_CONFIG = Path("configs/pipeline/b0.yaml")


async def _run(args: argparse.Namespace, config: PipelineConfig) -> ReviewResult:
    settings = get_settings()
    engine = create_engine(settings.database_url, pooled=False)
    try:
        async with open_providers(settings) as providers:
            return await run_review(
                engine,
                providers,
                repo_path=args.repo,
                base=args.base,
                head=args.head,
                repo_name=args.repo_name or f"local/{args.repo.resolve().name}",
                config=config,
                context_window=settings.llm_num_ctx,
                progress=lambda line: print(f"  .. {line}", flush=True),
            )
    finally:
        await engine.dispose()


def _print(result: ReviewResult, config: PipelineConfig, model: str) -> None:
    context = sum(len(c.context) for c in result.chunks)
    signature_only = sum(b.truncated for c in result.chunks for b in c.context)
    timing = ", ".join(f"{k} {v:.1f}s" for k, v in result.seconds.items())
    print(
        f"\nReview {result.review_id} [{result.status.value}] with {config.name} on {model}\n"
        f"  {len(result.changes.files)} files, {len(result.changes.seeds)} seeds, "
        f"{len(result.chunks)} chunk(s), {context} context blocks "
        f"({signature_only} signature-only)\n"
        f"  tokens {result.usage.input_tokens:,} in / {result.usage.output_tokens:,} out; {timing}"
    )
    if result.left_out:
        print(f"  left out (over max_chunks): {', '.join(result.left_out)}")
    for index, error in result.failed_chunks:
        print(f"  chunk {index + 1} FAILED: {error}")
    for summary in result.summaries:
        print(f"\n  {summary}")

    posted = result.posted
    print(f"\n{len(posted)} finding(s)")
    for c in posted:
        f = c.finding
        lines = f"{c.line_start}" if c.line_start == c.line_end else f"{c.line_start}-{c.line_end}"
        print(f"\n  [{f.severity.value}] {f.file_path}:{lines}  {f.title}")
        print(f"      {f.category.value}, confidence {f.confidence:.2f}")
        print(f"      {f.explanation}")
        if f.suggested_fix:
            print(f"      fix: {f.suggested_fix}")
        if f.evidence:
            print(f"      evidence: {', '.join(f.evidence)}")
    dropped = Counter(c.drop_reason for c in result.findings if c.status is FindingStatus.DROPPED)
    if dropped:
        print(
            f"\nDropped {sum(dropped.values())}: "
            + ", ".join(f"{k} {v}" for k, v in dropped.items())
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m core.review", description=__doc__.split("\n")[0]
    )
    parser.add_argument("repo", type=Path, help="path to a local git repository")
    parser.add_argument("--base", required=True, help="branch/commit the change is based on")
    parser.add_argument("--head", default="HEAD", help="branch/commit to review (default HEAD)")
    parser.add_argument("--repo-name", help='name used with core.index (default "local/<dir>")')
    parser.add_argument("--config", type=Path, help=f"pipeline config (default {DEFAULT_CONFIG})")
    args = parser.parse_args()

    config_path = args.config or (DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else None)
    config = PipelineConfig.from_yaml(config_path) if config_path else PipelineConfig()
    try:
        result = asyncio.run(_run(args, config))
    except (ReviewError, ProviderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    _print(result, config, get_settings().llm_model)


if __name__ == "__main__":
    main()
