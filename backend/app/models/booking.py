"""Four isolated fixture/business tables; booking service arrives in T03."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Row, SnapshotJSON, UTCDateTime, key_string, table_args


class Room(Row, Base):
    __tablename__ = "sb_rooms"
    __table_args__ = table_args(
        UniqueConstraint("scope_id", "room_code", name="uq_room_code_scope"),
        UniqueConstraint("scope_id", "id", name="uq_room_scope_id"),
        CheckConstraint("capacity > 0", name="ck_room_capacity"),
    )
    scope_id: Mapped[str] = mapped_column(key_string())
    room_code: Mapped[str] = mapped_column(key_string(32))
    capacity: Mapped[int] = mapped_column(Integer)
    opening_hours_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class BookingDraft(Row, Base):
    __tablename__ = "sb_booking_drafts"
    __table_args__ = table_args(
        UniqueConstraint("draft_uuid"),
        UniqueConstraint(
            "scope_id", "actor_key", "room_id", "id", name="uq_draft_scope_actor_room"
        ),
        ForeignKeyConstraint(
            ["scope_id", "room_id"],
            ["sb_rooms.scope_id", "sb_rooms.id"],
            name="fk_draft_room_scope",
        ),
        CheckConstraint("end_at > start_at AND attendees > 0", name="ck_draft_values"),
        CheckConstraint(
            "status IN ('pending','confirmed','expired','superseded','rejected')",
            name="ck_draft_status",
        ),
        Index("ix_draft_session", "scope_id", "actor_key", "session_id", "status"),
    )
    draft_uuid: Mapped[str] = mapped_column(key_string(36))
    scope_id: Mapped[str] = mapped_column(key_string())
    actor_key: Mapped[str] = mapped_column(key_string())
    session_id: Mapped[str | None] = mapped_column(key_string(36))
    room_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    start_at: Mapped[datetime] = mapped_column(UTCDateTime())
    end_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attendees: Mapped[int] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(key_string())
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Booking(Row, Base):
    __tablename__ = "sb_bookings"
    __table_args__ = table_args(
        UniqueConstraint("booking_uuid"),
        UniqueConstraint("draft_id", name="uq_booking_draft"),
        UniqueConstraint("scope_id", "actor_key", "idempotency_key", name="uq_booking_idempotency"),
        UniqueConstraint("scope_id", "room_id", "id", name="uq_booking_scope_room"),
        ForeignKeyConstraint(
            ["scope_id", "room_id"],
            ["sb_rooms.scope_id", "sb_rooms.id"],
            name="fk_booking_room_scope",
        ),
        ForeignKeyConstraint(
            ["scope_id", "actor_key", "room_id", "draft_id"],
            [
                "sb_booking_drafts.scope_id",
                "sb_booking_drafts.actor_key",
                "sb_booking_drafts.room_id",
                "sb_booking_drafts.id",
            ],
            name="fk_booking_draft_scope_actor",
        ),
        CheckConstraint("end_at > start_at AND attendees > 0", name="ck_booking_values"),
        CheckConstraint("status IN ('confirmed','cancelled')", name="ck_booking_status"),
    )
    booking_uuid: Mapped[str] = mapped_column(key_string(36))
    scope_id: Mapped[str] = mapped_column(key_string())
    actor_key: Mapped[str] = mapped_column(key_string())
    room_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    draft_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    start_at: Mapped[datetime] = mapped_column(UTCDateTime())
    end_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attendees: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="confirmed", server_default="confirmed")
    idempotency_key: Mapped[str] = mapped_column(key_string(128))
    payload_hash: Mapped[str] = mapped_column(key_string())


class BookingSlot(Row, Base):
    __tablename__ = "sb_booking_slots"
    __table_args__ = table_args(
        UniqueConstraint("scope_id", "room_id", "slot_start", name="uq_room_slot_scope"),
        ForeignKeyConstraint(
            ["scope_id", "room_id"], ["sb_rooms.scope_id", "sb_rooms.id"], name="fk_slot_room_scope"
        ),
        ForeignKeyConstraint(
            ["scope_id", "room_id", "booking_id"],
            ["sb_bookings.scope_id", "sb_bookings.room_id", "sb_bookings.id"],
            name="fk_slot_booking_scope_room",
        ),
        CheckConstraint(
            "MINUTE(slot_start) IN (0,30) AND SECOND(slot_start)=0 AND MICROSECOND(slot_start)=0",
            name="ck_slot_alignment",
        ),
    )
    scope_id: Mapped[str] = mapped_column(key_string())
    room_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    slot_start: Mapped[datetime] = mapped_column(UTCDateTime())
    booking_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
