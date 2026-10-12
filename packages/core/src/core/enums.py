"""Domain enums shared by the DB models, the parser, and the API.

They live outside `core.db` so the parser can use them without importing SQLAlchemy.
StrEnum members compare equal to their string values, which keeps JSON and DB I/O simple.
"""

from enum import StrEnum


class Language(StrEnum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"


class RepoSource(StrEnum):
    GITHUB = "github"
    LOCAL = "local"  # indexed from a path by the CLI


class IndexRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RepoStatus(StrEnum):
    PENDING = "pending"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class NodeKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


class EdgeKind(StrEnum):
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    CONTAINS = "contains"


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    SUGGESTION = "suggestion"


class ReviewSource(StrEnum):
    GITHUB = "github"  # a pull request
    CLI = "cli"  # a local branch: `python -m core.review`, or POST /api/v1/reviews
    EVAL = "eval"  # an evaluation run (Phase 3)


class FindingStatus(StrEnum):
    POSTED = "posted"  # passed validation
    DROPPED = "dropped"  # failed a validation rule; kept with the reason for traces/evals


class ReviewStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    # A newer push to the same pull request arrived first: this head is stale,
    # so it isn't reviewed (or, if already reviewed, isn't posted).
    SUPERSEDED = "superseded"
