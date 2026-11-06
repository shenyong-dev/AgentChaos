"""Cancellation intent is explicit; it is not a declined edit or a new booking."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.agents.registry import RequestLookup, ToolRegistry
from app.agents.runtime import confirmation_text
from app.schemas.booking import BookingToolResult, BookingView
from app.services import booking_changes
from app.services.booking import BookingError
from app.services.booking_changes import cancellation_declined, cancellation_requested


def test_cancel_intent_requires_an_existing_booking_request():
    assert cancellation_requested("明天我有事去不了，取消明天的预约")
    assert cancellation_requested("请撤销这个预约")
    assert cancellation_requested("把预约作废")
    assert not cancellation_requested("取消修改")
    assert not cancellation_requested("不用取消了")
    assert cancellation_declined("不用取消了")
    assert cancellation_declined("不想取消预约")
    assert not cancellation_requested("我不是要取消预约")
    assert not cancellation_requested("这不是取消预约")
    assert cancellation_requested("我特别想取消明天的预约")
    assert cancellation_declined("请别取消这个预约")
    assert not cancellation_requested("帮我预约明天")
    assert not cancellation_requested("确认当前完整草稿，请提交。")


def lookup_result(booking_id, request_key, status):
    now = datetime.now(UTC)
    return BookingToolResult[BookingView](
        code="request_found",
        summary="原记录",
        commit_state="committed",
        request_key=request_key,
        data=BookingView(
            booking_id=booking_id,
            draft_id=uuid4(),
            idempotency_key=request_key,
            room_code="A101",
            start_at=now,
            end_at=now + timedelta(hours=1),
            attendees=12,
            status=status,
            created_at=now,
        ),
    )


def cancel_state():
    booking_id, key = str(uuid4()), str(uuid4())
    return {
        "scope_id": "test:cancel-unit",
        "actor_key": "user:1",
        "session_id": str(uuid4()),
        "reference_clock": datetime.now(UTC).isoformat(),
        "turn_index": 3,
        "audit": [],
        "confirmation_state": "authorized",
        "confirmation_revision": 4,
        "pending_draft": {
            "operation": "cancel",
            "booking_id": booking_id,
            "draft_id": str(uuid4()),
            "draft_hash": "a" * 64,
            "original_request_key": key,
            "suggested_idempotency_key": str(uuid4()),
        },
        "grant": {"revision": 4},
        # The newest answer may describe a different, still valid booking.
        "business_result": {"data": {"idempotency_key": str(uuid4())}},
    }


@pytest.mark.parametrize("has_previous", [True, False])
def test_unknown_cancel_binds_the_cancelled_booking_instead_of_latest_answer(has_previous):
    state = cancel_state()
    if not has_previous:
        state.pop("business_result")
    error = BookingError("booking_state_unknown", "未知", 503, commit_state="unknown")
    result = ToolRegistry._error(state, "cancel_booking", error)
    assert result["request_key"] == state["pending_draft"]["original_request_key"]
    assert state["unknown_submission"] == {
        "request_key": result["request_key"],
        "operation": "cancel",
        "booking_id": state["pending_draft"]["booking_id"],
    }


@pytest.mark.parametrize("status", ["confirmed", "cancelled", "wrong_booking"])
def test_unknown_cancel_lookup_requires_the_exact_cancelled_record(status):
    state = cancel_state()
    ToolRegistry._error(
        state,
        "cancel_booking",
        BookingError("booking_state_unknown", "未知", 503, commit_state="unknown"),
    )
    unknown, draft = state["unknown_submission"].copy(), state["pending_draft"].copy()
    result = lookup_result(
        uuid4() if status == "wrong_booking" else draft["booking_id"],
        unknown["request_key"],
        "cancelled" if status == "cancelled" else "confirmed",
    )
    service = SimpleNamespace(lookup_booking_by_request=Mock(return_value=result))
    registry = ToolRegistry(service, None)
    values = RequestLookup(idempotency_key=unknown["request_key"])
    if status == "wrong_booking":
        with pytest.raises(Exception, match="查证记录"):
            registry._dispatch(state, "lookup_booking_by_request", values)
    else:
        response = registry._dispatch(state, "lookup_booking_by_request", values)
        if status == "cancelled":
            assert state["unknown_submission"] is None and state["pending_draft"] is None
            assert response["data"]["status"] == "cancelled"
        else:
            assert response["commit_state"] == "unknown"
    if status != "cancelled":
        assert state["unknown_submission"] == unknown and state["pending_draft"] == draft


def test_cancel_confirmation_requires_matching_grant_and_never_uses_create_tool():
    from app.agents.errors import AgentError
    from app.agents.registry import CancelBooking

    state = cancel_state()
    draft = state["pending_draft"]
    values = CancelBooking(
        booking_id=draft["booking_id"],
        draft_id=draft["draft_id"],
        draft_hash=draft["draft_hash"],
        idempotency_key=draft["suggested_idempotency_key"],
    )
    ToolRegistry._check_confirmation(state, values, booking_id=values.booking_id)
    with pytest.raises(AgentError):
        ToolRegistry._check_confirmation(state, values, booking_id=uuid4())
    state["confirmation_state"] = "rejected"
    with pytest.raises(AgentError):
        ToolRegistry._check_confirmation(state, values, booking_id=values.booking_id)
    assert "cancel_booking" in confirmation_text(draft, "confirm")
    assert confirmation_text(draft, "reject") == "拒绝撤销，保留原预约。"


@pytest.mark.parametrize("problem", ["expired", "changed"])
def test_cancel_expiry_or_changed_booking_cannot_release_slots(problem):
    now = datetime.now(UTC)
    row = SimpleNamespace(
        status="confirmed",
        booking_uuid=str(uuid4()),
        payload_hash="b" * 64,
        attendees=12,
        start_at=now,
        end_at=now + timedelta(hours=1),
        room_id=1,
    )
    session = Mock()
    session.scalar.return_value = row
    service = SimpleNamespace(_now=lambda: now)
    service._write = Mock(side_effect=lambda context, operation, **kwargs: operation(session))
    with pytest.raises(BookingError):
        booking_changes.commit_cancel(
            service,
            SimpleNamespace(scope_id="x", actor_key="y"),
            row.booking_uuid,
            "a" * 64,
            now - timedelta(seconds=1) if problem == "expired" else now + timedelta(minutes=1),
            request_key=str(uuid4()),
        )
    session.execute.assert_not_called()
    assert row.status == "confirmed"
    assert service._write.call_args.kwargs["request_key"]
