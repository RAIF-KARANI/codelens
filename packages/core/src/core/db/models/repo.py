from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base, IdMixin, TimestampMixin, str_enum
from core.enums import IndexRunStatus, RepoSource, RepoStatus

if TYPE_CHECKING:
    from core.db.models.code_graph import CodeNode


class Repo(IdMixin, TimestampMixin, Base):
    __tablename__ = "repos"

    # Nullable so the local indexing CLI can index repos that aren't on GitHub.
    github_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    full_name: Mapped[str] = mapped_column(unique=True)  # "owner/name" or "local/<dir>"
    source: Mapped[RepoSource] = mapped_column(
        str_enum(RepoSource, "repo_source"), server_default=RepoSource.GITHUB.value
    )
    local_path: Mapped[str | None]  # absolute path, for source = local
    default_branch: Mapped[str] = mapped_column(server_default="main")
    last_indexed_sha: Mapped[str | None]
    status: Mapped[RepoStatus] = mapped_column(
        str_enum(RepoStatus, "repo_status"), server_default=RepoStatus.PENDING.value
    )
    # The GitHub App installation this repo is reviewed through. SET NULL on
    # uninstall: the indexed graph and past reviews stay.
    installation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("installations.id", ondelete="SET NULL"), index=True
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
    is_test: Mapped[bool] = mapped_column(server_default="false")  # down-weighted in retrieval
    size_bytes: Mapped[int] = mapped_column(server_default="0")

    repo: Mapped[Repo] = relationship(back_populates="files", lazy="raise")
    nodes: Mapped[list["CodeNode"]] = relationship(
        back_populates="file", lazy="raise", passive_deletes=True
    )


class IndexRun(IdMixin, Base):
    """One execution of the indexer: history, and the stats it printed."""

    __tablename__ = "index_runs"

    repo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repos.id", ondelete="CASCADE"), index=True
    )
    commit_sha: Mapped[str | None]
    status: Mapped[IndexRunStatus] = mapped_column(
        str_enum(IndexRunStatus, "index_run_status"),
        server_default=IndexRunStatus.RUNNING.value,
    )
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    # Counts and timings (files changed, nodes by kind, % calls resolved, ...).
    # JSONB because the set of stats will grow with the pipeline.
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    error: Mapped[str | None]
