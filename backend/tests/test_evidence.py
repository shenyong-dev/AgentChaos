"""T06 real database/Redis evidence checks. Model gateway is explicitly offline."""

import asyncio
import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from agent_test_support import OfflineGateway
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate, save_evidence
from test_database import execution as schema_execution
from test_executor import add_case

from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.api.booking import get_booking_service
from app.api.evidence import get_trace_reader
from app.core.config import Settings
from app.db.seeds import seed_database
from app.evidence.reader import TraceReader, cursor
from app.evidence.recorder import EVIDENCE_ROOT, EvidenceRecorder, append_event
from app.evidence.streaming import SSESendDeadlineMiddleware
from app.executors.budget import OPEN_AUTHORIZATION, ExecutorBudget, authorization_limit
from app.executors.control import ExecutionControl
from app.executors.runner import run_execution
from app.executors.service import RunService
from app.main import create_app
from app.models import TestExecution, TestRun, TraceEvent
from app.schemas.runs import CreateRun, ExecutionInput
from app.services.booking import BookingService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AC_INTEGRATION") != "1", reason="Real MySQL/Redis, no paid calls"
    ),
]


@pytest.fixture(scope="module")
def infra():
    settings = Settings().model_copy(
        update={
            "executor_allow_paid_model_calls": True,
            "executor_budget_authorization_id": OPEN_AUTHORIZATION,
            "executor_agent_budget_unlimited": True,
            "executor_agent_budget_usd": 0,
            "executor_namespace": "ac:t06:verify:" + uuid4().hex,
            "sse_max_seconds": 5,
        }
    )
    redis = Redis.from_url(settings.redis_url, socket_timeout=15)
    paths = []
    with disposable_database("T06") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        runtime = AgentRuntime(engine, redis, settings, auto_register=True)
        service, reader = RunService(engine, settings), TraceReader(engine, settings)
        yield service, reader, redis, settings, runtime.version_id, paths
        with Session(engine) as session:
            for e in session.scalars(select(TestExecution)):
                runtime.saver.delete_thread(e.session_id)
        keys = list(redis.scan_iter(match=settings.executor_namespace + "*"))
        if keys:
            redis.delete(*keys)
    redis.close()
    for path in paths:
        assert path.is_relative_to(EVIDENCE_ROOT)
        if path.is_file():
            path.unlink()
        if path.parent.is_dir() and not any(path.parent.iterdir()):
            path.parent.rmdir()


@pytest.fixture
def trace(infra):
    service, reader, redis, settings, version, paths = infra

    def make(turns=None):
        identifier = service.create(
            1,
            CreateRun(
                request_id=uuid4(),
                target_version_id=version,
                case_revision_ids=[add_case(service.engine, turns)],
            ),
        )
        return identifier, service.read(1, identifier)["executions"][0]["execution_id"]

    yield service, reader, redis, settings, make, paths
    with Session(service.engine) as session:
        pending = list(
            session.scalars(
                select(TestRun.run_uuid).where(TestRun.status.in_(("queued", "running")))
            )
        )
    for identifier in pending:
        service.cancel(1, identifier)


def all_events(reader, identifier):
    found, after = [], None
    while True:
        page = reader.page(1, identifier, after=after, limit=25)
        found.extend(page["items"])
        if not page["has_more"]:
            return found
        after = page["next_cursor"]


def test_run_events_order_rollback_and_idempotency(trace):
    service, reader, _, settings, make, _ = trace
    identifier, _ = make()
    first = reader.page(1, identifier)["items"][0]["event"]
    assert first["type"] == "run.created" and first["execution_id"] is None
    assert first["attempt"] is None and first["scope"] == "run"
    stable = str(uuid4())

    def emit(index):
        with Session(service.engine) as session, session.begin():
            run = service.owned_run(session, 1, identifier, lock=True)
            row = append_event(
                session,
                run,
                None,
                {"kind": "run.resumed", "status": "queued", "reason": str(index)},
                settings=settings,
                event_id=stable if index == 0 else None,
            )
            return row.event_uuid

    emit(0)
    emit(0)
    before = reader.snapshot(1, identifier)["event_count"]
    with pytest.raises(RuntimeError), Session(service.engine) as session, session.begin():
        run = service.owned_run(session, 1, identifier, lock=True)
        append_event(session, run, None, {"kind": "run.resumed", "status": "queued"})
        assert reader.snapshot(1, identifier)["event_count"] == before
        raise RuntimeError("transaction deliberately rolled back")
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(emit, range(1, 7)))
    events = all_events(reader, identifier)
    assert [i["event"]["run_seq"] for i in events] == list(range(1, 9))
    assert len({i["event"]["event_id"] for i in events}) == 8
    with pytest.raises(AgentError) as error, Session(service.engine) as session, session.begin():
        run = service.owned_run(session, 1, identifier, lock=True)
        append_event(
            session,
            run,
            None,
            {"kind": "run.resumed", "status": "different"},
            event_id=stable,
            settings=settings,
        )
    assert error.value.code == "event_id_conflict"
    save_evidence("ordered-transactions.json", {"events": events}, task="T06")


def test_actual_graph_events_tools_confirmation_and_readonly_replay(trace):
    service, reader, redis, settings, make, _ = trace
    gateway = OfflineGateway()
    text = gateway.preview()
    identifier, execution = make([{"user": text}])
    run_execution(service.engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    waiting = service.read(1, identifier)["executions"][0]
    assert waiting["status"] == "waiting_input", waiting
    gateway.confirm(waiting["output"])
    service.supply_input(
        1,
        identifier,
        execution,
        ExecutionInput(
            request_id=uuid4(), expected_revision=waiting["timing"]["revision"], text="确认"
        ),
    )
    run_execution(service.engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    done = reader.execution(1, execution)
    assert done["status"] == "inconclusive" and len(done["after"]) == 1, done
    found = all_events(reader, identifier)
    events = [i["event"] for i in found]
    kinds = {i["type"] for i in events}
    assert {
        "model.started",
        "model.completed",
        "tool.requested",
        "tool.completed",
        "retrieval.completed",
        "confirmation.requested",
        "confirmation.resumed",
        "state.changed",
        "checkpoint.restored",
        "run.completed",
    } <= kinds
    mapped = {i["event_id"]: i for i in events}
    for item in events:
        if item["type"] in ("tool.completed", "tool.error"):
            parent = mapped[item["parent_event_id"]]
            assert parent["type"] == "tool.requested"
            assert item["tool_call_id"] == parent["tool_call_id"]
        if item["type"] == "model.completed":
            assert mapped[item["parent_event_id"]]["type"] == "model.started"
            assert item["payload"]["model_requests"] == 0
    model_visits = [
        i["node_visit"] for i in events if i["type"] == "node.started" and i["node"] == "model"
    ]
    assert model_visits == list(range(1, len(model_visits) + 1))
    calls = len(gateway.calls)
    for _ in range(2):
        assert all_events(reader, identifier) == found
        reader.snapshot(1, identifier)
    assert len(gateway.calls) == calls
    save_evidence(
        "offline-graph-events.json",
        {
            "events": found,
            "execution": done,
            "notice": "Explicit offline gateway, real MySQL/Redis/tools. Not provider acceptance.",
        },
        task="T06",
    )


def test_large_artifact_redaction_hash_and_owner(trace):
    service, reader, _, settings, make, paths = trace
    identifier, _ = make()
    secret = settings.deepseek_api_key.get_secret_value()
    with Session(service.engine) as session, session.begin():
        run = service.owned_run(session, 1, identifier, lock=True)
        event = append_event(
            session,
            run,
            None,
            {
                "kind": "run.completed",
                "status": "completed",
                "output": {
                    "authorization": "Bearer fake",
                    "password": "fixture-secret",
                    "text": "x" * 30000 + secret,
                },
            },
            settings=settings,
        )
        artifact_id = event.payload_json["payload"]["artifact"]["artifact_id"]
    path, digest = reader.artifact(1, identifier, artifact_id)
    paths.append(path)
    raw = path.read_text(encoding="utf-8")
    assert "fixture-secret" not in raw and "Bearer fake" not in raw
    assert not secret or secret not in raw
    assert json.loads(raw)["output"]["authorization"] == "[REDACTED]"
    for owner in (2, 99999):
        with pytest.raises(AgentError):
            reader.artifact(owner, identifier, artifact_id)
    path.write_text("tampered", encoding="utf-8")
    with pytest.raises(AgentError) as error:
        reader.artifact(1, identifier, artifact_id)
    assert error.value.code == "artifact_integrity_error"
    save_evidence(
        "artifact-check.json",
        {"sha256": digest, "redacted": True, "wrong_owner_denied": True, "tamper_denied": True},
        task="T06",
    )


def test_cursor_validation_and_authenticated_native_sse(trace, monkeypatch):
    import fastapi.routing

    service, reader, _, _, make, _ = trace
    identifier, execution = make()
    other, _ = make()
    app = create_app()
    app.dependency_overrides[get_trace_reader] = lambda: reader
    app.dependency_overrides[get_booking_service] = lambda: BookingService(service.engine)
    client = TestClient(app)
    base = f"/api/v1/runs/{identifier}/events"
    assert client.get(base + "/stream").status_code == 401
    client.auth = ("demo_operator", TEST_PASSWORD)
    assert client.get(base, params={"cursor": "invalid"}).status_code == 400
    assert client.get(base + "/stream", headers={"Last-Event-ID": cursor(other)}).status_code == 409
    assert (
        client.get(
            base + "/stream", headers={"Last-Event-ID": cursor(identifier, 9, uuid4())}
        ).status_code
        == 410
    )
    monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", 0.02)
    response = client.get(base + "/stream")
    assert response.status_code == 200 and response.headers["x-accel-buffering"] == "no"
    assert response.headers["content-type"].startswith("text/event-stream")
    assert ": ping" in response.text and "event: stream.rotate" in response.text
    first = reader.page(1, identifier)["items"][0]["cursor"]
    service.cancel(1, identifier)
    final = client.get(base + "/stream", headers={"Last-Event-ID": first})
    frames = final.text.replace("\r\n", "\n").split("\n\n")
    observed = [
        line[4:] for frame in frames for line in frame.splitlines() if line.startswith("id: ")
    ]
    expected = [i["cursor"] for i in reader.page(1, identifier, after=first)["items"]]
    assert observed == expected and "event: stream.end" in final.text
    assert client.get(f"/api/v1/executions/{execution}").status_code == 200
    save_evidence(
        "native-sse.json",
        {
            "headers": dict(response.headers),
            "heartbeat": True,
            "resumed_ids": observed,
            "terminal_body": final.text,
        },
        task="T06",
    )


def test_slow_send_deadline_unwinds_producer():
    closed = []

    async def app(scope, receive, send):
        try:
            await send({"type": "http.response.body", "body": b"fact"})
        finally:
            closed.append(True)

    async def slow(message):
        await asyncio.sleep(10)

    async def run():
        wrapped = SSESendDeadlineMiddleware(app, timeout=0.01)
        await asyncio.wait_for(wrapped({"type": "http", "path": "/events/stream"}, None, slow), 1)

    asyncio.run(run())
    assert closed == [True]


def test_unlimited_is_explicit_role_scoped_and_not_a_silent_cap_override():
    settings = Settings().model_copy(
        update={
            "executor_budget_authorization_id": OPEN_AUTHORIZATION,
            "executor_agent_budget_unlimited": True,
            "executor_agent_budget_usd": 0,
        }
    )
    assert authorization_limit(settings) == -1
    assert (
        authorization_limit(settings.model_copy(update={"executor_agent_budget_unlimited": False}))
        == 0
    )
    assert (
        authorization_limit(settings.model_copy(update={"executor_budget_authorization_id": "bad"}))
        == 0
    )


def test_open_ledger_keeps_finite_run_limits_and_late_usage(trace):
    service, reader, redis, settings, make, _ = trace
    identifier, execution_uuid = make()
    claim = service.claim(execution_uuid)
    control = ExecutionControl(service.engine, claim["id"], claim["token"])
    budget = ExecutorBudget(
        redis,
        settings,
        control,
        identifier,
        execution_uuid,
        prefix=settings.executor_namespace + ":open",
    )
    redis.hset(
        budget.key, mapping={"limit": -1, "calls_started": 500, "charged_estimate": 2_000_000}
    )
    recorder = EvidenceRecorder(control, settings, claim)
    started = recorder.emit("model.started", **recorder.model_metadata)
    ticket = budget.reserve(100, 10)
    service.cancel(1, identifier)
    budget.settle(ticket, {"input_tokens": 20, "output_tokens": 5})
    recorder.model_end(
        started,
        {
            "call_id": ticket["call_id"],
            "model_requests": 1,
            "usage": {"input_tokens": 20, "output_tokens": 5},
            "response": {"model_returned": "offline-late-observation"},
        },
    )
    events = all_events(reader, identifier)
    assert events[-1]["event"]["payload"]["late_observation"] is True
    assert events[-1]["event"]["parent_event_id"] == started
    assert reader.snapshot(1, identifier)["status"] == "cancelled"
    assert reader.snapshot(1, identifier)["pending_model_observations"] == 0
    assert budget.snapshot()["calls_started"] == 501
    limited = service.create(
        1,
        CreateRun(
            request_id=uuid4(),
            target_version_id=claim["manifest"]["target_version_id"],
            case_revision_ids=[add_case(service.engine)],
            budgets={"agent_microusd": 1},
        ),
    )
    execution = service.read(1, limited)["executions"][0]["execution_id"]
    other = service.claim(execution)
    bounded = ExecutorBudget(
        redis,
        settings,
        ExecutionControl(service.engine, other["id"], other["token"]),
        limited,
        execution,
        prefix=settings.executor_namespace + ":open",
    )
    with pytest.raises(AgentError) as error:
        bounded.reserve(100, 10)
    assert error.value.code == "model_budget_exhausted"


def test_model_error_is_observed_and_readonly_replay_does_not_retry(trace):
    service, reader, redis, settings, make, _ = trace
    identifier, execution = make()
    gateway = OfflineGateway()
    gateway.queue.append(AgentError("model_timeout", "explicit offline timeout", 504))
    run_execution(service.engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    events = all_events(reader, identifier)
    model_error = next(i for i in events if i["event"]["type"] == "model.error")
    assert model_error["event"]["payload"]["code"] == "model_timeout"
    assert reader.snapshot(1, identifier)["pending_model_observations"] == 0
    assert len(gateway.calls) == 1
    assert all_events(reader, identifier) == events
    assert len(gateway.calls) == 1


def test_native_route_slow_send_cancels_all_response_tasks(trace):
    service, reader, _, _, make, _ = trace
    identifier, _ = make()
    app = create_app()
    app.dependency_overrides[get_trace_reader] = lambda: reader
    app.dependency_overrides[get_booking_service] = lambda: BookingService(service.engine)
    sent = []

    async def run():
        async def receive():
            await asyncio.sleep(10)
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                await asyncio.sleep(10)
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": f"/api/v1/runs/{identifier}/events/stream",
            "query_string": b"",
            "headers": [
                (
                    b"authorization",
                    b"Basic " + base64.b64encode(f"demo_operator:{TEST_PASSWORD}".encode()),
                )
            ],
            "server": ("test", 80),
            "client": ("test", 1234),
            "root_path": "",
        }
        wrapped = SSESendDeadlineMiddleware(app, timeout=0.02)
        await asyncio.wait_for(wrapped(scope, receive, send), 3)

    asyncio.run(run())
    assert [i["type"] for i in sent] == ["http.response.start"]


def test_legacy_backfill_and_run_evidence_downgrade_guard():
    with disposable_database("T06") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        with Session(engine) as session, session.begin():
            e = schema_execution(session)
            run_id = e.run_id
            row = TraceEvent(
                event_uuid=str(uuid4()),
                execution_id=e.id,
                attempt_no=1,
                seq_no=1,
                event_type="schema.test",
                occurred_at=datetime.now(UTC),
                payload_json={"old": True},
                payload_hash="0" * 64,
            )
            session.add(row)
        migrate(engine, "downgrade", "20261025_0002")
        migrate(engine)
        with Session(engine) as session, session.begin():
            run = session.get(TestRun, run_id)
            row = session.scalar(select(TraceEvent))
            assert row.run_id == run_id and row.run_seq_no == run.event_seq == 1
            assert row.payload_json == {"old": True} and row.payload_hash == "0" * 64
            session.add(
                TraceEvent(
                    event_uuid=str(uuid4()),
                    run_id=run_id,
                    run_seq_no=0,
                    execution_id=None,
                    attempt_no=1,
                    seq_no=1,
                    event_type="schema.run",
                    occurred_at=datetime.now(UTC),
                    payload_json={},
                    payload_hash="0" * 64,
                )
            )
        with pytest.raises(RuntimeError, match="Run-level evidence exists"):
            migrate(engine, "downgrade", "20261025_0002")
