"""T02 fixture data only. No booking execution, global wipe, or implicit reset."""

import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Booking, BookingDraft, BookingSlot, Room, TestExecution

DEMO_SCOPE = "demo"
ROOMS = (("A101", 20, "21:00"), ("B201", 40, "21:00"), ("C301", 80, "18:00"))


class FixtureError(ValueError):
    pass


def validate_test_scope(scope: str):
    if not re.fullmatch(r"test:[A-Za-z0-9][A-Za-z0-9:_-]{0,58}", scope):
        raise FixtureError("An explicit test: scope of at most 64 characters is required")


def _assert_unregistered(session: Session, scope: str):
    # The unique scope index also supplies a next-key lock for absent scopes under
    # MySQL's default REPEATABLE READ. Caller owns the transaction until commit.
    execution = session.scalar(
        select(TestExecution.id).where(TestExecution.scope_id == scope).with_for_update()
    )
    if execution is not None:
        raise FixtureError("A recorded execution owns this scope; its evidence must be retained")


def ensure_rooms(session: Session, scope: str) -> int:
    inserted = 0
    for code, capacity, closing in ROOMS:
        hours = {"opens": "09:00", "closes": closing, "timezone": "Asia/Shanghai"}
        room = session.scalar(
            select(Room).where(Room.scope_id == scope, Room.room_code == code).with_for_update()
        )
        if room is None:
            session.add(
                Room(
                    scope_id=scope,
                    room_code=code,
                    capacity=capacity,
                    opening_hours_json=hours,
                    enabled=True,
                )
            )
            inserted += 1
        elif (room.capacity, room.opening_hours_json, room.enabled) != (capacity, hours, True):
            raise FixtureError("Existing fixture differs; no records were overwritten")
    session.flush()
    return inserted


def initialize_fixture(session: Session, scope: str) -> int:
    validate_test_scope(scope)
    _assert_unregistered(session, scope)
    return ensure_rooms(session, scope)


def clear_fixture(session: Session, scope: str) -> dict[str, int]:
    validate_test_scope(scope)
    _assert_unregistered(session, scope)
    session.execute(select(Room.id).where(Room.scope_id == scope).with_for_update()).all()
    removed = {}
    for model in (BookingSlot, Booking, BookingDraft, Room):
        result = session.execute(delete(model).where(model.scope_id == scope))
        removed[model.__tablename__] = result.rowcount
    session.flush()
    return removed
