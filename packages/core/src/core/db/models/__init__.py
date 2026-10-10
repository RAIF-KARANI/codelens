"""All ORM models. Importing this package registers every table on Base.metadata,
which Alembic needs to see the full schema."""

from core.db.models.anti_pattern import AntiPattern
from core.db.models.code_graph import CodeEdge, CodeNode
from core.db.models.repo import File, IndexRun, Repo
from core.db.models.review import Finding, Review

__all__ = [
    "AntiPattern",
    "CodeEdge",
    "CodeNode",
    "File",
    "Finding",
    "IndexRun",
    "Repo",
    "Review",
]
