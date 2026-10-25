"""MySQL schema primitives: explicit UTC and copied JSON snapshots."""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import MetaData, String, event, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, Mapper, mapped_column
from sqlalchemy.types import TypeDecorator


def snapshot(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))


class SnapshotJSON(TypeDecorator):
    impl = JSON(none_as_null=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return snapshot(value) if value is not None else None


class UTCDateTime(TypeDecorator):
    impl = DATETIME(fsp=6)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("An aware datetime is required")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Row:
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)")
    )


def key_string(length: int = 64) -> String:
    return String(length, collation="utf8mb4_bin")


def table_args(*constraints):
    return (
        *constraints,
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )


def _copy_on_set(target, value, old_value, initiator):
    return snapshot(value) if value is not None else None


@event.listens_for(Base, "mapper_configured", propagate=True)
def _copy_json_assignment(mapper: Mapper, cls: type):
    for prop in mapper.column_attrs:
        if isinstance(prop.columns[0].type, SnapshotJSON):
            event.listen(getattr(cls, prop.key), "set", _copy_on_set, retval=True)
