"""Real SQL/Redis with hostile offline tool requests, never paid model calls."""

from datetime import timedelta
from uuid import uuid4

import pytest
from agent_test_support import REFERENCE, OfflineGateway, message, resume, tool
from langchain_core.messages import AIMessage
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate, save_evidence

from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.core.config import Settings
from app.db.fixtures import initialize_fixture
from app.db.seeds import seed_database
from app.models import Booking, BookingDraft

pytestmark = [pytest.mark.integration]


@pytest.fixture(scope="module")
def infrastructure():
    import os

    if os.getenv("AC_INTEGRATION") != "1":
        pytest.skip("Real MySQL/Redis; no provider requests")
    settings = Settings()
    client = Redis.from_url(settings.redis_url, socket_timeout=15)
    try:
        with disposable_database("T17A") as engine:
            migrate(engine)
            seed_database(engine, TEST_PASSWORD)
            yield engine, client, settings
    finally:
        client.close()


@pytest.fixture
def scene(infrastructure):
    engine, client, settings = infrastructure
    scope = "test:t17a:" + uuid4().hex
    with Session(engine) as session, session.begin():
        initialize_fixture(session, scope)
    gateway = OfflineGateway()
    runtime = AgentRuntime(
        engine,
        client,
        settings,
        gateway=gateway,
        clock=lambda: REFERENCE,
        auto_register=True,
        prefix="ac:t17a:unit:" + uuid4().hex,
    )
    view = runtime.create(1, scope_id=scope, reference_clock=REFERENCE)
    try:
        yield runtime, gateway, view, scope
    finally:
        runtime.delete_test_session(1, view["session_id"])


def values(people=8):
    return {
        "start_at": (REFERENCE + timedelta(days=1, hours=5)).isoformat(),
        "end_at": (REFERENCE + timedelta(days=1, hours=6)).isoformat(),
        "attendees": people,
        "room_code": "A101",
    }


def export(runtime, scope, view, name):
    with Session(runtime.engine) as session:
        bookings = list(session.scalars(select(Booking).where(Booking.scope_id == scope)))
        drafts = list(session.scalars(select(BookingDraft).where(BookingDraft.scope_id == scope)))
        record = {
            "mode": "explicit_offline_gateway_real_SQL_Redis_LangGraph",
            "view": view,
            "history": runtime.history(1, view["session_id"]),
            "bookings": [{"id": row.id, "attendees": row.attendees} for row in bookings],
            "drafts": [{"id": row.id, "status": row.status} for row in drafts],
        }
    save_evidence(name, record, task="T17A")
    return bookings, drafts


def test_invalid_new_count_cannot_reuse_old_fact_or_confirm_old_draft(scene):
    runtime, gateway, initial, scope = scene
    old = runtime.act(1, initial["session_id"], message(initial, gateway.preview(attendees=8)))
    stale = resume(old)
    # Ignore the user's invalid update and deliberately try the old preview directly.
    gateway.queue.extend([tool("preview_booking", values()), AIMessage(content="请澄清人数。")])
    view = runtime.act(1, initial["session_id"], message(old, "人数改为-2人"))
    assert "attendees" not in view["fields"] and view["pending_draft"] is None
    with pytest.raises(AgentError):
        runtime.act(1, initial["session_id"], stale, kind="resume")
    gateway.queue.extend([tool("preview_booking", values()), AIMessage(content="仍需明确人数。")])
    view = runtime.act(1, initial["session_id"], message(view, "确认"))
    assert view["pending_draft"] is None and "attendees" not in view["fields"]
    bookings, drafts = export(runtime, scope, view, "offline-stale-count-blocked.json")
    assert not bookings and drafts and all(row.status == "superseded" for row in drafts)


def test_malicious_partial_quote_is_blocked_then_plain_number_can_recover(scene):
    runtime, gateway, initial, scope = scene
    old = runtime.act(1, initial["session_id"], message(initial, gateway.preview(attendees=8)))
    gateway.queue.extend(
        [
            tool("update_booking_request", {"attendees": 5, "source_quotes": {"attendees": "5人"}}),
            tool("preview_booking", values(5)),
            AIMessage(content="请补充有效人数。"),
        ]
    )
    view = runtime.act(1, initial["session_id"], message(old, "人数改为2.5人"))
    assert view["pending_draft"] is None and "attendees" not in view["fields"]
    gateway.queue.extend(
        [
            tool("update_booking_request", {"attendees": 12, "source_quotes": {"attendees": "12"}}),
            tool("query_availability", values(12)),
            tool("preview_booking", values(12)),
            AIMessage(content="新草稿12人，请确认。"),
        ]
    )
    view = runtime.act(1, initial["session_id"], message(view, "12"))
    assert view["pending_draft"] and view["fields"]["attendees"]["value"] == 12
    assert view["fields"]["attendees"]["source_turn"] == 3
    gateway.confirm(view)
    view = runtime.act(1, initial["session_id"], resume(view), kind="resume")
    bookings, _ = export(runtime, scope, view, "offline-clarification-recovered.json")
    assert len(bookings) == 1 and bookings[0].attendees == 12


def test_same_turn_correction_selects_new_count_and_ignores_other_numbers(scene):
    runtime, gateway, initial, scope = scene
    view = runtime.act(
        1,
        initial["session_id"],
        message(
            initial,
            gateway.preview(attendees=12, text="明天A101，14:00到15:00，不是8人，改为12人。"),
        ),
    )
    assert view["fields"]["attendees"]["value"] == 12
    gateway.confirm(view)
    view = runtime.act(1, initial["session_id"], resume(view), kind="resume")
    bookings, _ = export(runtime, scope, view, "offline-explicit-correction.json")
    assert len(bookings) == 1 and bookings[0].attendees == 12


def test_explicit_new_draft_after_refusal_reopens_without_self_authorization(scene):
    runtime, gateway, initial, scope = scene
    view = runtime.act(1, initial["session_id"], message(initial, gateway.preview(attendees=8)))
    gateway.queue.append(AIMessage(content="已取消旧草稿，等待明确人数。"))
    view = runtime.act(1, initial["session_id"], message(view, "人数改为-2人，先不要提交。"))
    assert view["confirmation_state"] == "rejected" and "attendees" not in view["fields"]
    gateway.queue.extend(
        [
            tool(
                "update_booking_request", {"attendees": 12, "source_quotes": {"attendees": "12人"}}
            ),
            tool("query_availability", values(12)),
            tool("preview_booking", values(12)),
            AIMessage(content="新草稿12人，尚未预约。"),
        ]
    )
    view = runtime.act(
        1, initial["session_id"], message(view, "人数更正为12人，请重新生成预约草稿。")
    )
    assert view["pending_draft"] and view["confirmation_state"] == "pending"
    bookings, _ = export(runtime, scope, view, "offline-reopen-before-confirm.json")
    assert not bookings
    gateway.confirm(view)
    view = runtime.act(1, initial["session_id"], resume(view), kind="resume")
    bookings, _ = export(runtime, scope, view, "offline-reopen-after-confirm.json")
    assert len(bookings) == 1 and bookings[0].attendees == 12
