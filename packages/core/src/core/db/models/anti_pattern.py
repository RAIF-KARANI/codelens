from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import EMBED_DIM, Base, IdMixin, TimestampMixin, str_enum
from core.enums import Severity


class AntiPattern(IdMixin, TimestampMixin, Base):
    __tablename__ = "anti_patterns"
    __table_args__ = (
        Index(
            "ix_anti_patterns_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    # NULL = global pattern that applies to every repo.
    repo_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("repos.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str]
    description: Mapped[str]
    example_code: Mapped[str | None]
    severity: Mapped[Severity] = mapped_column(str_enum(Severity, "anti_pattern_severity"))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    # How often reviews have matched this pattern; a ranking signal later.
    occurrences: Mapped[int] = mapped_column(server_default="0")
