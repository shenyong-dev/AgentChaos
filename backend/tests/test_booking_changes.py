"""Owned attendee amendments: real MySQL/Redis, no model provider calls."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from agent_test_support import OfflineGateway, message, resume, tool
from langchain_core.messages import AIMessage
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate

from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.core.config import Settings
from app.db.fixtures import initialize_fixture
from app.db.seeds import seed_database
from app.interactive.service import InteractionService
from app.models import Booking, BookingSlot
from app.schemas.booking import ConfirmBooking, PreviewBooking
from app.services import booking_changes
from app.services.booking import BookingContext, BookingError, BookingService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AC_INTEGRATION") != "1", reason="Real disposable SQL/Redis, no paid calls"
    ),
]
REFERENCE = datetime(2026, 10, 25, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def infrastructure():
    settings = Settings().model_copy(update={"allow_paid_model_calls": False})
    redis = Redis.from_url(settings.redis_url)
    with disposable_database("T17D") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        AgentRuntime(engine, redis, settings, gateway=OfflineGateway(), auto_register=True)
        yield engine, redis, settings
    redis.close()


@pytest.fixture
def booking(infrastructure):
    engine, _, _ = infrastructure
    context = BookingContext("test:change:" + uuid4().hex[:16], "user:1", REFERENCE, str(uuid4()))
    with Session(engine) as session, session.begin():
        initialize_fixture(session, context.scope_id)
    service = BookingService(engine, clock=lambda: REFERENCE)
    draft = service.preview_booking(
        context,
        PreviewBooking(
            room_code="A101",
            attendees=12,
            start_at=REFERENCE + timedelta(days=1, hours=5),
            end_at=REFERENCE + timedelta(days=1, hours=6),
        ),
    ).data
    original = service.confirm_booking(
        context,
        ConfirmBooking(
            draft_id=draft.draft_id,
            draft_hash=draft.draft_hash,
            idempotency_key=draft.suggested_idempotency_key,
            confirmed=True,
        ),
    ).data
    return service, context, original


def confirmation(draft, **changes):
    return ConfirmBooking(
        **{
            "draft_id": draft.draft_id,
            "draft_hash": draft.draft_hash,
            "idempotency_key": draft.suggested_idempotency_key,
            "confirmed": True,
            **changes,
        }
    )


def active(service, context):
    with Session(service.engine) as session:
        return [
            (b.booking_uuid, b.attendees, b.status)
            for b in session.scalars(
                select(Booking).where(Booking.scope_id == context.scope_id).order_by(Booking.id)
            )
        ]


def test_own_busy_slot_is_amended_only_after_confirmation_and_replayed(booking):
    service, context, original = booking
    draft = booking_changes.preview(service, context, original.booking_id, 10).data
    assert draft.previous_attendees == 12 and draft.attendees == 10
    assert active(service, context) == [(str(original.booking_id), 12, "confirmed")]
    with pytest.raises(BookingError):
        booking_changes.confirm(
            service, context, confirmation(draft, confirmed=False), original.booking_id
        )
    # The create endpoint cannot mistake an amendment hash for a normal new booking.
    with pytest.raises(BookingError, match="确认内容"):
        service.confirm_booking(context, confirmation(draft))
    result = booking_changes.confirm(service, context, confirmation(draft), original.booking_id)
    assert result.data.attendees == 10 and result.data.booking_id != original.booking_id
    assert (
        booking_changes.confirm(
            service, context, confirmation(draft), original.booking_id
        ).data.booking_id
        == result.data.booking_id
    )
    assert active(service, context) == [
        (str(original.booking_id), 12, "cancelled"),
        (str(result.data.booking_id), 10, "confirmed"),
    ]
    with Session(service.engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(BookingSlot)
                .where(BookingSlot.scope_id == context.scope_id)
            )
            == 2
        )


@pytest.mark.parametrize(
    "change", ["owner", "scope", "capacity", "zero", "same", "hash", "expired"]
)
def test_change_boundaries_keep_original_booking(booking, change):
    service, context, original = booking
    if change in {"owner", "scope", "capacity", "zero", "same"}:
        ctx = (
            replace(context, actor_key="user:2")
            if change == "owner"
            else (replace(context, scope_id="test:other") if change == "scope" else context)
        )
        amount = {"capacity": 21, "zero": 0, "same": 12}.get(change, 10)
        with pytest.raises((BookingError, ValueError)):
            booking_changes.preview(service, ctx, original.booking_id, amount)
    else:
        draft = booking_changes.preview(service, context, original.booking_id, 10).data
        if change == "expired":
            service.clock = lambda: REFERENCE + timedelta(minutes=6)
        values = confirmation(draft, **({"draft_hash": "0" * 64} if change == "hash" else {}))
        with pytest.raises(BookingError):
            booking_changes.confirm(service, context, values, original.booking_id)
    assert active(service, context) == [(str(original.booking_id), 12, "confirmed")]


def test_failed_slot_write_rolls_back_old_and_new_atomically(booking, monkeypatch):
    service, context, original = booking
    draft = booking_changes.preview(service, context, original.booking_id, 10).data

    def fail(*_):
        raise RuntimeError("synthetic write failure")

    monkeypatch.setattr(service, "_write_slots", fail)
    with pytest.raises(RuntimeError):
        booking_changes.confirm(service, context, confirmation(draft), original.booking_id)
    assert active(service, context) == [(str(original.booking_id), 12, "confirmed")]
    with Session(service.engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(BookingSlot)
                .where(BookingSlot.scope_id == context.scope_id)
            )
            == 2
        )


def test_concurrent_changes_only_one_wins(booking):
    service, context, original = booking
    a = booking_changes.preview(service, context, original.booking_id, 10).data
    other = replace(context, session_id=str(uuid4()))
    b = booking_changes.preview(service, other, original.booking_id, 11).data

    def submit(pair):
        ctx, draft = pair
        try:
            return booking_changes.confirm(
                service, ctx, confirmation(draft), original.booking_id
            ).data.attendees
        except BookingError:
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, [(context, a), (other, b)]))
    assert sum(x is not None for x in results) == 1
    assert len([x for x in active(service, context) if x[2] == "confirmed"]) == 1


def test_chat_change_preserves_context_rejects_without_grant_and_confirms(infrastructure):
    engine, redis, settings = infrastructure
    gateways = {}
    service = InteractionService(
        engine,
        redis,
        settings,
        clock=lambda: REFERENCE,
        gateway_factory=lambda sid: gateways.setdefault(sid, OfflineGateway()),
    )
    view = service.create(
        1, {"request_id": str(uuid4()), "version_id": service.access()["version_id"]}
    )
    sid = view["session_id"]
    gateway = gateways[sid]
    try:
        view = service.act(1, sid, message(view, gateway.preview(attendees=12)), "message")
        gateway.confirm(view)
        view = service.act(1, sid, resume(view), "resume")
        original = view["business_result"]["data"]["booking_id"]
        gateway.queue.extend(
            [
                tool(
                    "preview_attendee_change",
                    {"booking_id": original, "attendees": 10, "source_quote": "10人"},
                ),
                AIMessage(content="12人改为10人的变更草稿，待确认，原预约仍有效。"),
            ]
        )
        view = service.act(1, sid, message(view, "现在帮我修改刚才的预约，人数改为10人"), "message")
        assert view["pending_draft"]["operation"] == "attendee_change"
        assert view["pending_draft"]["previous_attendees"] == 12
        runtime = service.runtime(sid)
        _, state, _ = runtime.snapshot(1, sid)
        denied = runtime.registry.dispatch(
            state,
            "confirm_booking",
            {
                "draft_id": view["pending_draft"]["draft_id"],
                "draft_hash": view["pending_draft"]["draft_hash"],
                "idempotency_key": view["pending_draft"]["suggested_idempotency_key"],
            },
        )
        assert denied["code"] == "confirmation_not_authorized"
        gateway.confirm(view)
        view = service.act(1, sid, resume(view), "resume")
        assert view["business_result"]["data"]["attendees"] == 10
        assert view["business_result"]["data"]["booking_id"] != original
        assert view["sql_observation"]["booking_count"] == 1
        assert view["sql_observation"]["booking_record_count"] == 2
        history = service.history(1, sid, 0, 100)["items"]
        assert any("修改刚才" in str(m["content"]) for m in history)
    finally:
        service.runtime(sid).delete_test_session(1, sid)


def test_explicit_upgrade_keeps_messages_and_sql_without_model_or_budget_reset(infrastructure):
    engine, redis, settings = infrastructure
    gateway = OfflineGateway()
    service = InteractionService(
        engine, redis, settings, clock=lambda: REFERENCE, gateway_factory=lambda _: gateway
    )
    view = service.create(
        1, {"request_id": str(uuid4()), "version_id": service.access()["version_id"]}
    )
    sid = view["session_id"]
    runtime = service.runtime(sid)
    key = runtime._key(sid)
    try:
        view = service.act(1, sid, message(view, gateway.preview(attendees=12)), "message")
        gateway.confirm(view)
        view = service.act(1, sid, resume(view), "resume")
        old_history = service.history(1, sid, 0, 100)
        raw = json.loads(redis.get(key))
        raw["config_hash"] = "0" * 64
        redis.set(key, json.dumps(raw))
        assert service.view(1, sid)["read_only"]
        count = len(gateway.calls)
        with pytest.raises(AgentError):
            service.upgrade(
                2, sid, {"request_id": str(uuid4()), "expected_revision": view["revision"]}
            )
        body = {"request_id": str(uuid4()), "expected_revision": view["revision"]}
        legacy_key = f"ac:t17d:budget:T17D-20261102-agent-1:agent:session:{sid}"
        redis.hset(legacy_key, mapping={"calls_started": 3, "charged_estimate": 17})
        with pytest.raises(AgentError) as error:
            service.upgrade(1, sid, body)
        assert error.value.code == "session_budget_binding_changed"
        assert service.view(1, sid)["usage"]["calls_started"] == 3
        redis.delete(legacy_key)
        redis.set(key + ":pending", "uncertain")
        with pytest.raises(AgentError):
            service.upgrade(1, sid, body)
        redis.delete(key + ":pending")
        upgraded = service.upgrade(1, sid, body)
        assert not upgraded["read_only"] and upgraded["revision"] == view["revision"] + 1
        assert service.history(1, sid, 0, 100) == old_history
        assert (
            len(gateway.calls) == count and upgraded["business_result"] == view["business_result"]
        )
        assert service.upgrade(1, sid, body)["revision"] == upgraded["revision"]
        assert redis.exists(key + ":upgrade:" + body["request_id"])
        gateway.queue.extend(
            [
                tool(
                    "preview_attendee_change",
                    {
                        "booking_id": upgraded["business_result"]["data"]["booking_id"],
                        "attendees": 10,
                        "source_quote": "10人",
                    },
                ),
                AIMessage(content="人数变更待确认。"),
            ]
        )
        following = service.act(1, sid, message(upgraded, "把刚才的预约改为10人"), "message")
        assert following["pending_draft"]["previous_attendees"] == 12
    finally:
        runtime.delete_test_session(1, sid)


def test_rejected_change_can_be_followed_by_new_explicit_change(infrastructure):
    engine, redis, settings = infrastructure
    gateway = OfflineGateway()
    service = InteractionService(
        engine, redis, settings, clock=lambda: REFERENCE, gateway_factory=lambda _: gateway
    )
    view = service.create(
        1, {"request_id": str(uuid4()), "version_id": service.access()["version_id"]}
    )
    sid = view["session_id"]
    try:
        view = service.act(1, sid, message(view, gateway.preview(attendees=12)), "message")
        gateway.confirm(view)
        view = service.act(1, sid, resume(view), "resume")
        booking_id = view["business_result"]["data"]["booking_id"]

        def request(amount):
            gateway.queue.extend(
                [
                    tool(
                        "preview_attendee_change",
                        {
                            "booking_id": booking_id,
                            "attendees": amount,
                            "source_quote": f"{amount}人",
                        },
                    ),
                    AIMessage(content="变更草稿待确认。"),
                ]
            )

        request(10)
        view = service.act(1, sid, message(view, "修改人数为10人"), "message")
        gateway.queue.append(AIMessage(content="已拒绝变更，原预约保留。"))
        view = service.act(1, sid, resume(view, "reject"), "resume")
        assert view["pending_draft"] is None
        request(11)
        view = service.act(1, sid, message(view, "把刚才的预约人数改为11人"), "message")
        assert view["pending_draft"]["attendees"] == 11
        gateway.queue.append(AIMessage(content="已拒绝变更。"))
        view = service.act(1, sid, resume(view, "reject"), "resume")
        request(11)
        view = service.act(1, sid, message(view, "不用修改了"), "message")
        assert view["pending_draft"] is None
        last_tool = next(item for item in reversed(view["trace"]) if item.get("type") == "tool")
        assert last_tool["result"]["code"] == "user_declined"
    finally:
        service.runtime(sid).delete_test_session(1, sid)


def test_cancel_preview_keeps_the_booking_until_the_same_authorization(booking):
    service, context, original = booking
    with Session(service.engine) as session:
        before = session.scalar(
            select(func.count())
            .select_from(BookingSlot)
            .where(BookingSlot.scope_id == context.scope_id)
        )
    preview = booking_changes.preview_cancel(service, context, original.booking_id)
    assert preview.commit_state == "not_committed"
    assert preview.data.operation == "cancel"
    assert active(service, context) == [(str(original.booking_id), 12, "confirmed")]
    changed = booking_changes.preview(service, context, original.booking_id, 9).data
    booking_changes.confirm(service, context, confirmation(changed), original.booking_id)
    with pytest.raises(BookingError) as error:
        booking_changes.commit_cancel(
            service,
            context,
            original.booking_id,
            preview.data.draft_hash,
            preview.data.expires_at,
            request_key=preview.data.original_request_key,
        )
    assert error.value.code == "booking_changed"
    with Session(service.engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(BookingSlot)
                .where(BookingSlot.scope_id == context.scope_id)
            )
            == before
        )


def test_chat_cancel_releases_slots_only_after_confirmation(infrastructure):
    engine, redis, settings = infrastructure
    gateway = OfflineGateway()
    service = InteractionService(
        engine, redis, settings, clock=lambda: REFERENCE, gateway_factory=lambda _: gateway
    )
    view = service.create(
        1, {"request_id": str(uuid4()), "version_id": service.access()["version_id"]}
    )
    sid = view["session_id"]
    try:
        view = service.act(1, sid, message(view, gateway.preview(attendees=12)), "message")
        gateway.confirm(view)
        view = service.act(1, sid, resume(view), "resume")
        booking_id = view["business_result"]["data"]["booking_id"]
        assert view["sql_observation"]["booking_count"] == 1
        gateway.queue.extend(
            [
                tool("preview_cancel_booking", {"booking_id": booking_id}),
                AIMessage(content="撤销待确认，原预约仍然有效。"),
            ]
        )
        view = service.act(1, sid, message(view, "明天我有事去不了，取消明天的预约"), "message")
        draft = view["pending_draft"]
        assert draft["operation"] == "cancel"
        assert view["sql_observation"]["booking_count"] == 1
        runtime = service.runtime(sid)
        _, state, _ = runtime.snapshot(1, sid)
        denied = runtime.registry.dispatch(
            state,
            "cancel_booking",
            {
                "booking_id": booking_id,
                "draft_id": draft["draft_id"],
                "draft_hash": draft["draft_hash"],
                "idempotency_key": draft["suggested_idempotency_key"],
            },
        )
        assert denied["code"] == "confirmation_not_authorized"
        blocked = runtime.registry.dispatch(
            state,
            "confirm_booking",
            {
                "draft_id": draft["draft_id"],
                "draft_hash": draft["draft_hash"],
                "idempotency_key": draft["suggested_idempotency_key"],
            },
        )
        assert blocked["code"] == "cancel_tool_required"
        gateway.queue.extend(
            [
                tool(
                    "cancel_booking",
                    {
                        "booking_id": booking_id,
                        "draft_id": draft["draft_id"],
                        "draft_hash": draft["draft_hash"],
                        "idempotency_key": draft["suggested_idempotency_key"],
                    },
                ),
                AIMessage(content="预约已取消，时段已释放。"),
            ]
        )
        view = service.act(1, sid, resume(view), "resume")
        assert view["business_result"]["data"]["status"] == "cancelled"
        assert view["business_result"]["data"]["booking_id"] == booking_id
        assert view["sql_observation"]["booking_count"] == 0
        assert view["sql_observation"]["booking_record_count"] == 1
        assert view["pending_draft"] is None
    finally:
        service.runtime(sid).delete_test_session(1, sid)
