"""T17D real SQL/Redis controls with an explicit offline model; no paid requests."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from agent_test_support import REFERENCE, OfflineGateway, message, resume, tool
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate

from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.api.booking import get_booking_service
from app.api.interactive import get_interaction_service
from app.core.config import Settings
from app.db.seeds import seed_database
from app.interactive.budget import AUTHORIZATION, InteractionBudget
from app.interactive.service import InteractionService
from app.main import create_app
from app.models import Room
from app.services.booking import BookingError, BookingService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AC_INTEGRATION") != "1", reason="Disposable MySQL and scoped Redis required"
    ),
]


@pytest.fixture(scope="module")
def infrastructure():
    settings = Settings()
    client = Redis.from_url(settings.redis_url, socket_timeout=15)
    with disposable_database("T17D") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        AgentRuntime(engine, client, settings, gateway=OfflineGateway(), auto_register=True)
        yield engine, client, settings
    client.close()


@pytest.fixture
def fixture(infrastructure):
    engine, client, settings = infrastructure
    gateways = {}
    service = InteractionService(
        engine,
        client,
        settings,
        gateway_factory=lambda key: gateways.setdefault(key, OfflineGateway()),
        clock=lambda: REFERENCE,
    )
    views = []

    def create(user=1, body=None):
        body = body or {"request_id": str(uuid4()), "version_id": service.access()["version_id"]}
        view = service.create(user, body)
        views.append((user, view))
        return view

    yield service, gateways, create
    cleaned = set()
    for user, view in views:
        if view["session_id"] in cleaned:
            continue
        cleaned.add(view["session_id"])
        runtime = service.runtime(view["session_id"])
        runtime.delete_test_session(user, view["session_id"])
        keys = list(client.scan_iter(runtime._key(view["session_id"]) + ":*"))
        if keys:
            client.delete(*keys)


def preview(fixture, attendees=8):
    service, gateways, create = fixture
    view = create()
    gateway = gateways[view["session_id"]]
    view = service.act(
        1, view["session_id"], message(view, gateway.preview(attendees=attendees)), "message"
    )
    return service, gateway, view


def test_creation_is_owner_and_version_bound_idempotent_and_never_calls_model(fixture):
    service, gateways, create = fixture
    body = {"request_id": str(uuid4()), "version_id": service.access()["version_id"]}
    a = create(body=body)
    b = create(body=body)
    assert a["session_id"] == b["session_id"] and a["revision"] == b["revision"] == 0
    assert a["scope_id"].startswith("test:interactive:") and a["kind"] == "booking_agent"
    assert not gateways[a["session_id"]].calls and a["sql_observation"]["booking_count"] == 0
    with Session(service.engine) as session:
        assert len(list(session.scalars(select(Room).where(Room.scope_id == a["scope_id"])))) == 3
    with pytest.raises(AgentError, match="所选版本"):
        create(body={**body, "request_id": str(uuid4()), "version_id": 99999})
    for operation in [
        lambda: service.view(9999, a["session_id"]),
        lambda: service.history(9999, a["session_id"], 0, 10),
        lambda: service.trace(9999, a["session_id"], 0, 10),
        lambda: service.receipt(9999, a["session_id"], body["request_id"]),
    ]:
        with pytest.raises(AgentError) as error:
            operation()
        assert error.value.status_code == 404


def test_confirmation_receipt_replay_and_reconnect_do_not_repeat_booking(fixture):
    service, gateway, view = preview(fixture)
    assert view["fields"]["attendees"]["value"] == 8
    assert view["fields"]["attendees"]["source_turn"] == 1
    assert (
        view["sql_observation"]["booking_count"] == 0 and view["status"] == "waiting_confirmation"
    )
    gateway.confirm(view)
    body = resume(view)
    result = service.act(1, view["session_id"], body, "resume")
    count = len(gateway.calls)
    assert service.receipt(1, view["session_id"], body["request_id"])["status"] == "completed"
    replay = service.act(1, view["session_id"], body, "resume")
    assert (
        result["sql_observation"]["booking_count"]
        == replay["sql_observation"]["booking_count"]
        == 1
    )
    assert len(gateway.calls) == count and not replay["processing"]
    assert service.view(1, view["session_id"])["revision"] == result["revision"]
    with pytest.raises(AgentError) as error:
        service.act(1, view["session_id"], {**body, "decision": "reject"}, "resume")
    assert error.value.code == "action_id_conflict"


def test_missing_attendees_then_modified_attendees_invalidates_old_confirmation(fixture):
    service, gateways, create = fixture
    view = create()
    gateway = gateways[view["session_id"]]
    gateway.queue.append(AIMessage(content="离线控制：请补充人数。"))
    view = service.act(1, view["session_id"], message(view, "明天A101，14:00到15:00。"), "message")
    assert view["pending_draft"] is None and view["sql_observation"]["booking_count"] == 0
    view = service.act(
        1, view["session_id"], message(view, gateway.preview(attendees=8)), "message"
    )
    old = resume(view)
    draft = view["pending_draft"]
    args = {k: draft[k] for k in ("room_code", "start_at", "end_at")}
    args["attendees"] = 12
    gateway.queue.extend(
        [
            tool(
                "update_booking_request", {"attendees": 12, "source_quotes": {"attendees": "12人"}}
            ),
            tool("query_availability", args),
            tool("preview_booking", args),
            AIMessage(content="离线控制：修改后重新确认。"),
        ]
    )
    view = service.act(1, view["session_id"], message(view, "人数改为12人。"), "message")
    assert (
        view["fields"]["attendees"]["value"] == 12
        and view["fields"]["attendees"]["source_turn"] == 3
    )
    assert any(
        x["draft_id"] == draft["draft_id"] and x["status"] == "superseded"
        for x in view["sql_observation"]["drafts"]
    )
    with pytest.raises(AgentError) as error:
        service.act(
            1,
            view["session_id"],
            {**old, "request_id": str(uuid4()), "expected_revision": view["revision"]},
            "resume",
        )
    assert error.value.code == "confirmation_stale"
    gateway.confirm(view)
    result = service.act(1, view["session_id"], resume(view), "resume")
    assert result["sql_observation"]["booking_count"] == 1
    assert result["sql_observation"]["bookings"][0]["attendees"] == 12


def test_rejection_has_no_business_write_and_history_trace_are_paginated(fixture):
    service, gateway, view = preview(fixture)
    gateway.queue.append(AIMessage(content="离线控制：已拒绝，不提交。"))
    result = service.act(1, view["session_id"], resume(view, "reject"), "resume")
    assert (
        result["confirmation_state"] == "rejected"
        and result["sql_observation"]["booking_count"] == 0
    )
    assert any(x["status"] == "rejected" for x in result["sql_observation"]["drafts"])
    page = service.history(1, view["session_id"], 0, 1)
    assert len(page["items"]) == 1 and page["next_cursor"] == 1
    trace = service.trace(1, view["session_id"], 0, 1)
    assert len(trace["items"]) == 1 and trace["total"] > 1 and trace["next_cursor"] == 1


def test_unknown_tool_receipt_is_separate_from_current_sql_and_original_key_lookup(
    fixture, monkeypatch
):
    service, gateway, view = preview(fixture)
    original = BookingService.confirm_booking

    def lost(self, context, values):
        original(self, context, values)
        raise BookingError(
            "controlled_receipt_lost",
            "明确控制：提交后回执丢失",
            503,
            commit_state="unknown",
            request_key=values.idempotency_key,
        )

    monkeypatch.setattr(BookingService, "confirm_booking", lost)
    gateway.confirm(view)
    result = service.act(1, view["session_id"], resume(view), "resume")
    assert result["unknown_submission"] and result["sql_observation"]["booking_count"] == 1
    assert result["request_lookup"]["commit_state"] == "committed"
    before = len(gateway.calls)
    assert service.view(1, view["session_id"])["sql_observation"]["booking_count"] == 1
    assert len(gateway.calls) == before


def test_busy_and_unresolved_receipts_block_new_actions_without_model(fixture):
    service, gateways, create = fixture
    view = create()
    runtime = service.runtime(view["session_id"])
    key = runtime._key(view["session_id"])
    pending_id = str(uuid4())
    service.redis.set(key + ":pending", pending_id)
    service.redis.set(
        key + ":receipt:" + pending_id,
        json.dumps({"request_id": pending_id, "status": "processing"}),
    )
    body = message(view, "新的预约")
    with pytest.raises(AgentError) as error:
        service.act(1, view["session_id"], body, "message")
    assert (
        error.value.code == "interactive_receipt_unknown" and not gateways[view["session_id"]].calls
    )
    assert service.receipt(1, view["session_id"], pending_id)["status"] == "processing"
    assert service.redis.get(key + ":pending")  # GET receipt does not change pending state.
    lock = service.redis.lock(key + ":operation", timeout=30)
    lock.acquire()
    try:
        with pytest.raises(AgentError) as error:
            service.act(1, view["session_id"], body, "message")
        assert error.value.code == "session_busy"
    finally:
        lock.release()


def test_concurrent_duplicate_message_uses_one_model_path(fixture):
    service, gateways, create = fixture
    view = create()
    gateway = gateways[view["session_id"]]
    started, release = Event(), Event()

    def wait(_):
        started.set()
        assert release.wait(10)
        return AIMessage(content="离线控制：已收到。")

    gateway.queue.append(wait)
    body = message(view, "查询场地信息")
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(service.act, 1, view["session_id"], body, "message")
        assert started.wait(10)
        try:
            with pytest.raises(AgentError) as error:
                service.act(1, view["session_id"], body, "message")
            assert error.value.code == "session_busy"
        finally:
            release.set()
        first.result()
    service.act(1, view["session_id"], body, "message")
    assert len(gateway.calls) == 1


def test_api_requires_operator_and_rejects_client_scope_or_clock(fixture):
    service, _, create = fixture
    view = create()
    app = create_app()
    app.dependency_overrides[get_interaction_service] = lambda: service
    app.dependency_overrides[get_booking_service] = lambda: BookingService(service.engine)
    with TestClient(app) as client:
        assert client.get("/api/v1/interactive/access").status_code == 401
        assert (
            client.get(
                "/api/v1/interactive/access", auth=("demo_reviewer", TEST_PASSWORD)
            ).status_code
            == 403
        )
        auth = ("demo_operator", TEST_PASSWORD)
        assert (
            client.get("/api/v1/interactive/access", auth=auth).json()["data"]["version_id"]
            == view["version_id"]
        )
        response = client.post(
            "/api/v1/interactive/sessions",
            auth=auth,
            json={
                "request_id": str(uuid4()),
                "version_id": view["version_id"],
                "scope_id": "demo",
                "owner_id": 999,
            },
        )
        assert response.status_code == 400
        assert (
            client.get(
                f"/api/v1/interactive/sessions/{view['session_id']}/history?limit=101", auth=auth
            ).status_code
            == 400
        )


def test_paid_switch_off_keeps_view_readable_and_sends_no_model(fixture):
    service, gateways, create = fixture
    view = create()
    real = InteractionService(
        service.engine, service.redis, service.settings, clock=lambda: REFERENCE
    )
    assert not real.access()["model_actions_available"]
    assert real.view(1, view["session_id"])["sql_observation"]["booking_count"] == 0
    with pytest.raises(AgentError) as error:
        real.act(1, view["session_id"], message(view, "预约"), "message")
    assert error.value.code == "interactive_not_authorized"
    assert not gateways[view["session_id"]].calls


def test_closed_budget_still_replays_a_completed_request_without_new_call(fixture):
    service, gateway, view = preview(fixture)
    gateway.confirm(view)
    body = resume(view)
    service.act(1, view["session_id"], body, "resume")
    before = len(gateway.calls)
    real = InteractionService(
        service.engine, service.redis, service.settings, clock=lambda: REFERENCE
    )
    replay = real.act(1, view["session_id"], body, "resume")
    assert replay["sql_observation"]["booking_count"] == 1 and len(gateway.calls) == before


def test_old_session_version_is_rejected_and_metadata_is_not_rewritten(fixture):
    service, _, create = fixture
    view = create()
    runtime = service.runtime(view["session_id"])
    key = runtime._key(view["session_id"])
    raw = service.redis.get(key)
    metadata = json.loads(raw)
    metadata["config_hash"] = "0" * 64
    changed = json.dumps(metadata)
    service.redis.set(key, changed)
    try:
        with pytest.raises(AgentError) as error:
            service.view(1, view["session_id"])
        assert error.value.code == "session_version_mismatch"
        assert service.redis.get(key).decode() == changed
    finally:
        service.redis.set(key, raw)


def test_interaction_budget_missing_global_and_session_caps_and_unknown_retention(
    infrastructure, tmp_path
):
    _, redis, original = infrastructure
    settings = original.model_copy(
        update={
            "allow_paid_model_calls": True,
            "model_budget_authorization_id": AUTHORIZATION,
            "agent_budget_usd": 1,
        }
    )
    budget = InteractionBudget(redis, settings, str(uuid4()), directory=tmp_path)
    budget.key = "ac:t17d:testbudget:" + uuid4().hex
    budget.session_key = budget.key + ":session"
    budget.calls_key = budget.session_key + ":calls"
    try:
        with pytest.raises(AgentError):
            budget.reserve(100, 1024)
        redis.hset(budget.key, mapping={"limit": 1_000_000})
        ticket = budget.reserve(100, 1024)
        budget.unknown(ticket)
        held = budget.snapshot()["held_upper"]
        assert held > 0 and budget.snapshot()["calls_unknown"] == 1
        assert budget.settle(ticket, {"input_tokens": 10, "output_tokens": 10}) == 15
        assert budget.snapshot()["held_upper"] == 0 and budget.snapshot()["calls_unknown"] == 0
        redis.hset(budget.calls_key, "calls_started", 20)
        with pytest.raises(AgentError):
            budget.reserve(100, 1024)
        redis.hset(budget.calls_key, "calls_started", 0)
        redis.hset(budget.session_key, "charged_estimate", 100_000)
        with pytest.raises(AgentError):
            budget.reserve(100, 1024)
        redis.hset(budget.session_key, "charged_estimate", 0)
        redis.hset(budget.key, "calls_started", 120)
        with pytest.raises(AgentError):
            budget.reserve(100, 1024)
    finally:
        keys = list(redis.scan_iter(budget.key + "*"))
        if keys:
            redis.delete(*keys)


def test_same_user_and_source_in_different_actual_database_cannot_read_session(fixture):
    service, _, create = fixture
    body = {"request_id": str(uuid4()), "version_id": service.access()["version_id"]}
    view = create(body=body)
    with disposable_database("T17D") as other_engine:
        migrate(other_engine)
        seed_database(other_engine, TEST_PASSWORD)
        AgentRuntime(
            other_engine,
            service.redis,
            service.settings,
            gateway=OfflineGateway(),
            auto_register=True,
        )
        other = InteractionService(
            other_engine,
            service.redis,
            service.settings,
            gateway_factory=lambda _: OfflineGateway(),
            clock=lambda: REFERENCE,
        )
        assert other.binding != service.binding and other.prefix != service.prefix
        with pytest.raises(AgentError) as error:
            other.view(1, view["session_id"])
        assert error.value.status_code == 404
        new = other.create(1, {**body, "version_id": other.access()["version_id"]})
        assert new["session_id"] != view["session_id"] and new["scope_id"] != view["scope_id"]
        other.runtime(new["session_id"]).delete_test_session(1, new["session_id"])
