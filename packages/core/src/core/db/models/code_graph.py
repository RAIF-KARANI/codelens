"""The code graph: nodes are definitions, edges are relationships between them."""

from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import EMBED_DIM, Base, IdMixin, str_enum
from core.enums import EdgeKind, NodeKind

if TYPE_CHECKING:
    from core.db.models.repo import File


class CodeNode(IdMixin, Base):
    __tablename__ = "code_nodes"
    __table_args__ = (
        CheckConstraint("end_line >= start_line", name="line_range"),
        # Not unique: Python legally redefines names, e.g. a @property getter and
        # its @x.setter are two `def x` in the same class.
        Index("ix_code_nodes_repo_id_qualified_name", "repo_id", "qualified_name"),
        # HNSW gives approximate nearest-neighbour search. vector_cosine_ops means
        # queries must use the cosine-distance operator (<=>) to hit this index.
        Index(
            "ix_code_nodes_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_code_nodes_tsv", "tsv", postgresql_using="gin"),
    )

    # repo_id duplicates file.repo_id on purpose: retrieval filters by repo on
    # every query, and this avoids a join on the hot path.
    repo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("repos.id", ondelete="CASCADE"))
    # Postgres does not index foreign keys automatically. Re-indexing a file
    # deletes its nodes by file_id, so that lookup needs an index.
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[NodeKind] = mapped_column(str_enum(NodeKind, "node_kind"))
    name: Mapped[str]
    qualified_name: Mapped[str]  # e.g. "pkg.module.Class.method"
    signature: Mapped[str | None]
    start_line: Mapped[int]  # 1-based, inclusive
    end_line: Mapped[int]
    code: Mapped[str]
    docstring: Mapped[str | None]
    content_hash: Mapped[str]
    # PageRank over the call graph. NULL until the graph-analysis job runs.
    centrality: Mapped[float | None]
    # NULL until the embedding job runs (Phase 2).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    # Written by the indexer, not a generated column: identifiers like
    # getUserById are split into sub-words in Python first, then stored with
    # to_tsvector('simple', ...), with no English stemming.
    tsv: Mapped[str | None] = mapped_column(TSVECTOR)

    file: Mapped["File"] = relationship(back_populates="nodes", lazy="raise")


class CodeEdge(IdMixin, Base):
    __tablename__ = "code_edges"
    __table_args__ = (
        # Partial index: just the unresolved edges, which is exactly what the
        # re-link pass scans after a re-index.
        Index(
            "ix_code_edges_unresolved",
            "repo_id",
            "target_name",
            postgresql_where=text("dst_node_id IS NULL"),
        ),
    )

    repo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("repos.id", ondelete="CASCADE"))
    src_node_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("code_nodes.id", ondelete="CASCADE"), index=True
    )
    # NULL means unresolved: an external library, a dynamic call, or a target
    # whose node was deleted during re-indexing. SET NULL (not CASCADE) keeps
    # the edge, so it can be re-linked by target_name afterwards.
    dst_node_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("code_nodes.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[EdgeKind] = mapped_column(str_enum(EdgeKind, "edge_kind"))
    # The target as best resolved by name (e.g. "pkg.utils.helper" or just
    # "requests.get"). Always set, so a dangling edge can be re-resolved.
    target_name: Mapped[str]
