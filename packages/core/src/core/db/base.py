"""Declarative base, shared column mixins, and the enum column helper."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, Enum, Identity, MetaData, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.config import get_settings

# Read once at import. Models and the migration both use it, so
# `alembic check` stays consistent with the dimension the DB was created with.
EMBED_DIM = get_settings().embed_dim

# Deterministic constraint/index names. Without this Postgres invents names, and
# Alembic can't reliably drop or alter those constraints in later migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Postgres TEXT has no length limit and the same performance as VARCHAR(n),
    # so plain `Mapped[str]` maps to TEXT. All timestamps are timezone-aware.
    type_annotation_map = {  # noqa: RUF012 — SQLAlchemy reads this class attribute
        str: Text,
        datetime: DateTime(timezone=True),
    }


class IdMixin:
    # BIGINT identity rather than SERIAL: identity columns are the SQL-standard
    # replacement, and code_nodes/code_edges can plausibly exceed 2^31 rows.
    # sort_order: mixin columns would otherwise land after the subclass's own
    # columns; this keeps `id` first and timestamps last in the table.
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, sort_order=-1)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=1)


class TimestampMixin(CreatedAtMixin):
    # onupdate is applied by the ORM on UPDATE; raw SQL updates won't bump it.
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), sort_order=2
    )


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """VARCHAR + CHECK constraint instead of a native Postgres ENUM type.

    Native enums need `ALTER TYPE ... ADD VALUE`, which has transaction caveats
    and can't remove values. A CHECK constraint is changed with an ordinary,
    fully transactional migration. Values (not member names) are stored.
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )
