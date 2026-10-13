"""Request and response bodies of /api/v1 (planning.md §7). These are the API's
contract, kept separate from the ORM models so the schema can change without
changing what clients see."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.enums import (
    FindingStatus,
    IndexRunStatus,
    RepoSource,
    RepoStatus,
    ReviewSource,
    ReviewStatus,
    Severity,
)


class _FromRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    """Keyset pagination: pass `next_cursor` back as `?cursor=` for the next
    page; null means this is the last one."""

    items: list[T]
    next_cursor: int | None


# --- repos


class Repo(_FromRow):
    id: int
    full_name: str
    source: RepoSource
    default_branch: str
    status: RepoStatus
    last_indexed_sha: str | None
    created_at: datetime
    updated_at: datetime
    last_review_at: datetime | None = None


class IndexRun(_FromRow):
    id: int
    commit_sha: str | None
    status: IndexRunStatus
    started_at: datetime
    finished_at: datetime | None
    stats: dict[str, Any]
    error: str | None


class RepoDetail(Repo):
    latest_index_run: IndexRun | None


class IndexQueued(BaseModel):
    repo_id: int
    status: str = "queued"


# --- reviews


class SeverityCounts(BaseModel):
    """Posted findings by severity."""

    critical: int = 0
    warning: int = 0
    suggestion: int = 0


class ReviewSummary(_FromRow):
    id: int
    repo_id: int
    source: ReviewSource
    pr_number: int | None
    base_sha: str | None
    head_sha: str
    status: ReviewStatus
    model: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    # The review posted on the pull request (github source only), once posted.
    github_review_id: int | None = None
    counts: SeverityCounts = Field(default_factory=SeverityCounts)


class Finding(_FromRow):
    id: int
    file_path: str
    line_start: int
    line_end: int
    severity: Severity
    category: str
    title: str
    explanation: str
    suggested_fix: str | None
    evidence_node_ids: list[int]
    confidence: float | None
    status: FindingStatus
    drop_reason: str | None


class ReviewDetail(ReviewSummary):
    token_usage: dict[str, int] | None
    pipeline_config: dict[str, Any]
    findings: list[Finding]


class ReviewCreate(BaseModel):
    """A manual review of a local repo's branch: head against its merge-base
    with base, like a pull request. (`{repo_id, pr_number}` for GitHub repos
    arrives with the GitHub App.)"""

    repo_id: int
    base: str = Field(min_length=1, max_length=255, examples=["main"])
    head: str = Field(min_length=1, max_length=255, examples=["my-feature"])


class ReviewQueued(BaseModel):
    review_id: int
    status: ReviewStatus
    head_sha: str


# --- the dashboard's review views (Phase 6)


class Votes(BaseModel):
    up: int = 0
    down: int = 0


class FindingWithVotes(Finding):
    votes: Votes = Field(default_factory=Votes)
    my_vote: Literal[-1, 1] | None = None  # the caller's own 👍/👎


class ReviewDetailWithVotes(ReviewDetail):
    findings: list[FindingWithVotes]  # type: ignore[assignment]


class Seed(BaseModel):
    """A changed node: what the review started from."""

    path: str
    qualified_name: str
    kind: str
    change: Literal["added", "modified", "deleted"]
    start_line: int
    end_line: int
    signature_changed: bool


class ReviewDiff(BaseModel):
    review_id: int
    base_sha: str | None
    head_sha: str
    diff: str  # unified diff, as git produced it
    seeds: list[Seed]


class TraceItem(BaseModel):
    """One retrieved candidate, and whether it made it into the prompt."""

    label: str  # how the prompt named it, e.g. "N12"
    node_id: int | None  # NULL once a re-index deleted the node
    qualified_name: str
    included: bool
    truncated: bool  # included as its signature only
    token_cost: int
    stage_scores: dict[str, Any]


class TraceChunk(BaseModel):
    index: int
    items: list[TraceItem]


class ReviewTrace(BaseModel):
    review_id: int
    budgets: dict[str, Any] | None  # token budgets per prompt section ("packed" event)
    chunks: list[TraceChunk]


class GraphNode(BaseModel):
    id: str  # "n<node id>", or "seed:<qualified name>" for a node the index doesn't have yet
    node_id: int | None
    label: str
    qualified_name: str
    kind: str
    path: str | None
    is_seed: bool
    change: str | None  # for seeds: added / modified / deleted
    depth: int | None  # 0 seed, 1 caller or callee; None if only retrieved as context
    in_context: bool  # shown to the model


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: Literal["calls", "inherits"]


class ReviewGraph(BaseModel):
    review_id: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class CodeNode(BaseModel):
    id: int
    repo_id: int
    path: str
    kind: str
    name: str
    qualified_name: str
    signature: str | None
    docstring: str | None
    start_line: int
    end_line: int
    code: str


class FeedbackIn(BaseModel):
    vote: Literal[-1, 1]


class FeedbackOut(BaseModel):
    finding_id: int
    votes: Votes
    my_vote: Literal[-1, 1] | None
