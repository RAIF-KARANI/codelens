"""All ORM models. Importing this package registers every table on Base.metadata,
which Alembic needs to see the full schema."""

from core.db.models.anti_pattern import AntiPattern
from core.db.models.code_graph import CodeEdge, CodeNode
from core.db.models.github import Installation, WebhookDelivery
from core.db.models.repo import File, IndexRun, Repo
from core.db.models.review import (
    Finding,
    FindingFeedback,
    Review,
    ReviewContextItem,
    ReviewEvent,
)

__all__ = [
    "AntiPattern",
    "CodeEdge",
    "CodeNode",
    "File",
    "Finding",
    "FindingFeedback",
    "IndexRun",
    "Installation",
    "Repo",
    "Review",
    "ReviewContextItem",
    "ReviewEvent",
    "WebhookDelivery",
]
