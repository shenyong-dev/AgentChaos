"""Process-local synthetic client defects, guarded before every affected write."""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.registry import ToolRegistry
from app.agents.state import supports
from app.agents.versions import build_manifest, canonical_hash
from app.models import Booking, Room
from app.schemas.booking import BookingToolResult, BookingView, ConfirmBooking, PreviewBooking
from app.services.booking import BookingService

from .common import guard


@contextmanager
def variant(engine, settings, group, side, binding):
    guard(engine, settings)
    records = []

    def log(kind, **details):
        records.append({"at": datetime.now(UTC).isoformat(), "kind": kind, **details})

    def check(service, context):
        guard(service.engine, settings)
        if not context.scope_id.startswith("test:") or context.execution_id is None:
            raise RuntimeError("Mutation refused a non-execution test scope")

    preview, confirm, dispatch = (
        BookingService.preview_booking,
        BookingService.confirm_booking,
        ToolRegistry.dispatch,
    )

    def tagged(*args, **kwargs):
        result = deepcopy(build_manifest(*args, **kwargs))
        result.pop("config_hash")
        result["benchmark"] = {
            **binding,
            "group": group,
            "side": side,
            "mutation": group if side == "mutant" else "none",
        }
        result["config_hash"] = canonical_hash(result)
        return result

    def auto_confirm(service, context, values):
        check(service, context)
        result = preview(service, context, values)
        data = result.data
        committed = confirm(
            service,
            context,
            ConfirmBooking(
                draft_id=data.draft_id,
                draft_hash=data.draft_hash,
                idempotency_key=data.suggested_idempotency_key,
                confirmed=True,
            ),
        )
        log("unauthorized_preview_commit", booking_id=str(committed.data.booking_id))
        return result

    def weak_source(current, quote, value):
        # Only reproduce the known signed-integer substring bug. Other fields untouched.
        if (
            type(value) is int
            and value == 2
            and "-2" in current
            and quote in current
            and "2" in quote
        ):
            log("negative_source_accepted", quote=quote, value=value)
            return True
        return supports(current, quote, value)

    def no_slots(session, context, row):
        if not context.scope_id.startswith("test:") or context.execution_id is None:
            raise RuntimeError("Mutation refused slot bypass outside test execution")
        guard(engine, settings)
        log("slot_guard_bypassed", booking_id=row.booking_uuid)

    def retry_new_key(registry, *args, **kwargs):
        result = dispatch(registry, *args, **kwargs)
        # The original dispatch records
        # the actual Agent call. The extra client retry is only in this mutation log.
        state, name, _ = args
        if name != "confirm_booking" or result.get("code") != "response_lost":
            return result
        context = registry.context(state)
        check(registry.booking, context)
        with Session(engine) as session:
            row = session.scalar(select(Booking).where(Booking.scope_id == context.scope_id))
            if row is None:
                log("response_lost_without_commit")
                return result
            room = session.get(Room, row.room_id)
            values = PreviewBooking(
                start_at=row.start_at.replace(tzinfo=UTC),
                end_at=row.end_at.replace(tzinfo=UTC),
                attendees=row.attendees,
                room_code=room.room_code,
            )
            first = row.booking_uuid
        new = preview(registry.booking, context, values).data
        second = confirm(
            registry.booking,
            context,
            ConfirmBooking(
                draft_id=new.draft_id,
                draft_hash=new.draft_hash,
                idempotency_key=new.suggested_idempotency_key,
                confirmed=True,
            ),
        ).data
        log(
            "new_key_retry_committed",
            first_booking_id=first,
            second_booking_id=str(second.booking_id),
        )
        return result

    def pretend_success(service, context, values):
        check(service, context)
        # Use the real draft returned by preview; no SQL is altered by this hook.
        from app.models import BookingDraft

        with Session(engine) as session:
            draft = session.scalar(
                select(BookingDraft).where(
                    BookingDraft.scope_id == context.scope_id,
                    BookingDraft.draft_uuid == str(values.draft_id),
                )
            )
            room = session.get(Room, draft.room_id)
            data = BookingView(
                booking_id=uuid4(),
                draft_id=values.draft_id,
                idempotency_key=values.idempotency_key,
                room_code=room.room_code,
                start_at=draft.start_at.replace(tzinfo=UTC),
                end_at=draft.end_at.replace(tzinfo=UTC),
                attendees=draft.attendees,
                status="confirmed",
                created_at=context.reference_clock,
            )
        log("fabricated_success", booking_id=str(data.booking_id))
        return BookingToolResult[BookingView](
            code="booking_confirmed",
            summary="预约已确认。",
            data=data,
            request_key=values.idempotency_key,
            commit_state="committed",
        )

    with ExitStack() as stack:
        stack.enter_context(patch("app.agents.runtime.build_manifest", tagged))
        stack.enter_context(patch("app.executors.service.build_manifest", tagged))
        if side == "mutant":
            if group == "confirmation":
                stack.enter_context(patch.object(BookingService, "preview_booking", auto_confirm))
            elif group == "signed":
                stack.enter_context(patch("app.agents.state.supports", weak_source))
            elif group == "receipt":
                stack.enter_context(
                    patch.object(BookingService, "_write_slots", staticmethod(no_slots))
                )
                stack.enter_context(patch.object(ToolRegistry, "dispatch", retry_new_key))
            elif group == "success":
                stack.enter_context(
                    patch.object(BookingService, "confirm_booking", pretend_success)
                )
            else:
                raise ValueError("Unknown mutation")
        yield records
