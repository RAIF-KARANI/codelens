from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base, IdMixin, TimestampMixin, str_enum
from core.enums import RepoStatus

if TYPE_CHECKING:
    from core.db.models.code_graph import CodeNode


class Repo(IdMixin, TimestampMixin, Base):
    __tablename__ = "repos"

    # Nullable so the local indexing CLI can index repos that aren't on GitHub.
    github_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    full_name: Mapped[str] = mapped_column(unique=True)  # "owner/name"
    default_branch: Mapped[str] = mapped_column(server_default="main")
    last_indexed_sha: Mapped[str | None]
    status: Mapped[RepoStatus] = mapped_column(
        str_enum(RepoStatus, "repo_status"), server_default=RepoStatus.PENDING.value
    )

    # lazy="raise": async sessions can't lazy-load anyway, so make any accidental
    # attribute access fail loudly. Load explicitly with selectinload() instead.
    # passive_deletes: let the DB's ON DELETE CASCADE do the work.
    files: Mapped[list["File"]] = relationship(
        back_populates="repo", lazy="raise", passive_deletes=True
    )


class File(IdMixin, Base):
    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("repo_id", "path"),)

    repo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("repos.id", ondelete="CASCADE"))
    path: Mapped[str]  # POSIX-style, relative to the repo root
    language: Mapped[str]
    # sha256 of the file bytes. The indexer skips files whose hash is unchanged.
    content_hash: Mapped[str]

    repo: Mapped[Repo] = relationship(back_populates="files", lazy="raise")
    nodes: Mapped[list["CodeNode"]] = relationship(
        back_populates="file", lazy="raise", passive_deletes=True
    )
