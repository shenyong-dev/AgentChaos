"""Confirmed attendee changes replace an owned booking in one transaction."""

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select, update

from app.models import Booking, BookingDraft, BookingSlot, Room
from app.schemas.booking import (
    AttendeeChangeDraft,
    AvailabilityQuery,
    BookingToolResult,
    CancelBookingDraft,
)
from app.services.booking import (
    DRAFT_TTL,
    BookingError,
    payload_hash,
    validate_room,
    validate_times,
)


def owned(session, context, booking_id):
    row = session.scalar(
        select(Booking)
        .where(
            Booking.scope_id == context.scope_id,
            Booking.actor_key == context.actor_key,
            Booking.booking_uuid == str(booking_id),
        )
        .with_for_update()
    )
    if row is None:
        raise BookingError("booking_not_found", "当前身份下找不到这笔预约。", 404)
    return row


def change_hash(context, original, values):
    return hashlib.sha256(
        json.dumps(
            {
                "operation": "attendee_change_v1",
                "booking_id": original.booking_uuid,
                "original_hash": original.payload_hash,
                "original_attendees": original.attendees,
                "new_payload_hash": payload_hash(context, original.room_id, values),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def preview(service, context, booking_id, attendees):
    def operation(session):
        original = owned(session, context, booking_id)
        if original.status != "confirmed":
            raise BookingError("booking_not_active", "原预约已取消或已被替换，请查看最新预约。")
        request = AvailabilityQuery(
            start_at=original.start_at, end_at=original.end_at, attendees=attendees
        )
        validate_times(context, request)
        room = session.scalar(select(Room).where(Room.id == original.room_id).with_for_update())
        validate_room(room, request)
        if attendees == original.attendees:
            raise BookingError("booking_unchanged", "人数与当前预约相同，无需再次提交。", 400)
        session.execute(
            update(BookingDraft)
            .where(
                BookingDraft.scope_id == context.scope_id,
                BookingDraft.actor_key == context.actor_key,
                BookingDraft.session_id == context.session_id,
                BookingDraft.status == "pending",
            )
            .values(status="superseded")
        )
        draft = BookingDraft(
            draft_uuid=str(uuid4()),
            scope_id=context.scope_id,
            actor_key=context.actor_key,
            session_id=context.session_id,
            room_id=original.room_id,
            start_at=original.start_at,
            end_at=original.end_at,
            attendees=attendees,
            payload_hash=change_hash(context, original, request),
            status="pending",
            expires_at=service._now() + DRAFT_TTL,
        )
        session.add(draft)
        session.flush()
        view = AttendeeChangeDraft(
            draft_id=draft.draft_uuid,
            draft_hash=draft.payload_hash,
            suggested_idempotency_key=draft.draft_uuid,
            room_code=room.room_code,
            start_at=draft.start_at,
            end_at=draft.end_at,
            attendees=attendees,
            status=draft.status,
            expires_at=draft.expires_at,
            booking_id=original.booking_uuid,
            previous_attendees=original.attendees,
        )
        return BookingToolResult[AttendeeChangeDraft](
            code="attendee_change_confirmation_required",
            summary="确认后原预约将被原子替换，旧记录保留并生成新预约号；确认前原预约仍有效。",
            data=view,
            commit_state="not_committed",
            request_key=view.suggested_idempotency_key,
            evidence_refs=[f"booking:{original.booking_uuid}", f"draft:{draft.draft_uuid}"],
        )

    return service._write(context, operation)


def confirm(service, context, values, booking_id):
    if not values.confirmed:
        raise BookingError("confirmation_required", "尚未确认修改，原预约保持不变。", 400)

    def operation(session):
        original = owned(session, context, booking_id)
        draft = session.scalar(
            select(BookingDraft)
            .where(
                BookingDraft.scope_id == context.scope_id,
                BookingDraft.actor_key == context.actor_key,
                BookingDraft.session_id == context.session_id,
                BookingDraft.draft_uuid == str(values.draft_id),
            )
            .with_for_update()
        )
        if draft is None:
            raise BookingError("draft_not_found", "当前会话找不到变更草稿。", 404)
        request = AvailabilityQuery(
            start_at=draft.start_at, end_at=draft.end_at, attendees=draft.attendees
        )
        if (
            draft.room_id != original.room_id
            or draft.start_at != original.start_at
            or draft.end_at != original.end_at
            or not hmac.compare_digest(draft.payload_hash, values.draft_hash)
            or not hmac.compare_digest(draft.payload_hash, change_hash(context, original, request))
        ):
            raise BookingError("draft_mismatch", "变更与原预约或确认内容不一致。")
        previous = session.scalar(
            select(Booking).where(
                Booking.scope_id == context.scope_id,
                Booking.actor_key == context.actor_key,
                Booking.idempotency_key == str(values.idempotency_key),
            )
        )
        if previous:
            if previous.draft_id != draft.id or previous.payload_hash != draft.payload_hash:
                raise BookingError("idempotency_conflict", "同一标识不能用于不同修改。")
            return service._booking_result(session, previous, "booking_change_replayed")
        if original.status != "confirmed" or draft.status != "pending":
            raise BookingError("booking_change_stale", "原预约或变更草稿已失效，请重新核对。")
        if service._now() >= draft.expires_at:
            raise BookingError("draft_expired", "变更草稿已过期，原预约保持不变。", 410)
        validate_times(context, request)
        room = session.scalar(select(Room).where(Room.id == original.room_id).with_for_update())
        validate_room(room, request)
        # Only the original's own slots may be replaced. Other bookings remain protected.
        conflict = session.scalar(
            select(BookingSlot.id)
            .where(
                BookingSlot.scope_id == context.scope_id,
                BookingSlot.room_id == room.id,
                BookingSlot.slot_start >= draft.start_at,
                BookingSlot.slot_start < draft.end_at,
                BookingSlot.booking_id != original.id,
            )
            .limit(1)
        )
        if conflict:
            raise BookingError("slot_conflict", "时段存在其他占用，原预约保持不变。")
        original.status = "cancelled"
        session.execute(
            delete(BookingSlot).where(
                BookingSlot.scope_id == context.scope_id, BookingSlot.booking_id == original.id
            )
        )
        row = Booking(
            booking_uuid=str(uuid4()),
            scope_id=context.scope_id,
            actor_key=context.actor_key,
            room_id=room.id,
            draft_id=draft.id,
            start_at=draft.start_at,
            end_at=draft.end_at,
            attendees=draft.attendees,
            idempotency_key=str(values.idempotency_key),
            payload_hash=draft.payload_hash,
            status="confirmed",
        )
        session.add(row)
        session.flush()
        service._write_slots(session, context, row)
        draft.status, draft.confirmed_at = "confirmed", service._now()
        session.flush()
        return service._booking_result(session, row, "booking_attendees_changed")

    return service._write(
        context, operation, request_key=values.idempotency_key, booking_effect=True
    )


def cancellation_declined(text: str) -> bool:
    return bool(
        re.search(
            r"(?:不要|(?<!特)别|无需|不用|不可以|不能|不想|不是|先不).{0,3}(?:取消|撤销|作废)"
            r"|不(?:取消|撤销|作废)",
            text,
        )
    )


def cancellation_requested(text: str) -> bool:
    if cancellation_declined(text):
        return False
    if re.search(r"(?:取消|撤销).{0,6}(?:修改|变更|调整)", text) and "预约" not in text:
        return False
    return bool(re.search(r"(?:取消|撤销|作废).{0,16}预约|预约.{0,12}(?:取消|撤销|作废)", text))


def _instant(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def cancel_hash(row) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "operation": "cancel_booking_v1",
                "booking_id": row.booking_uuid,
                "payload_hash": row.payload_hash,
                "status": row.status,
                "attendees": row.attendees,
                "start_at": _instant(row.start_at),
                "end_at": _instant(row.end_at),
                "room_id": row.room_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def preview_cancel(service, context, booking_id):
    def operation(session):
        original = owned(session, context, booking_id)
        if original.status != "confirmed":
            raise BookingError("booking_not_active", "原预约已取消或已被替换，请查看最新预约。")
        room = session.get(Room, original.room_id)
        session.execute(
            update(BookingDraft)
            .where(
                BookingDraft.scope_id == context.scope_id,
                BookingDraft.actor_key == context.actor_key,
                BookingDraft.session_id == context.session_id,
                BookingDraft.status == "pending",
            )
            .values(status="superseded")
        )
        draft_id = uuid4()
        view = CancelBookingDraft(
            draft_id=draft_id,
            draft_hash=cancel_hash(original),
            suggested_idempotency_key=draft_id,
            room_code=room.room_code,
            start_at=original.start_at,
            end_at=original.end_at,
            attendees=original.attendees,
            status="pending",
            expires_at=service._now() + DRAFT_TTL,
            booking_id=original.booking_uuid,
            original_request_key=original.idempotency_key,
        )
        return BookingToolResult[CancelBookingDraft](
            code="cancel_confirmation_required",
            summary="确认后将释放该预约占用的时段，状态变为已取消，历史记录保留。确认前预约仍然有效。",
            data=view,
            commit_state="not_committed",
            request_key=view.suggested_idempotency_key,
            evidence_refs=[f"booking:{original.booking_uuid}"],
        )

    return service._write(context, operation)


def commit_cancel(service, context, booking_id, expected_hash, expires_at, *, request_key):
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)

    def operation(session):
        original = owned(session, context, booking_id)
        if service._now() >= expires_at:
            raise BookingError("draft_expired", "撤销确认已过期，原预约保持不变。", 410)
        if original.status != "confirmed" or not hmac.compare_digest(
            cancel_hash(original), expected_hash
        ):
            raise BookingError("booking_changed", "预约已变化或已取消，请重新核对后再撤销。")
        session.execute(select(Room.id).where(Room.id == original.room_id).with_for_update()).all()
        session.execute(
            delete(BookingSlot).where(
                BookingSlot.scope_id == context.scope_id,
                BookingSlot.booking_id == original.id,
            )
        )
        original.status = "cancelled"
        session.flush()
        return service._booking_result(
            session,
            original,
            "booking_cancelled",
            "预约已取消，占用时段已释放，历史记录保留。",
        )

    return service._write(context, operation, request_key=request_key, booking_effect=True)
