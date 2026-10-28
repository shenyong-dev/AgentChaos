"""T07 real MySQL/Redis/booking transactions with an explicit offline model double."""

import json
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from agent_test_support import OfflineGateway, tool
from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage
from pydantic import ValidationError
from redis import Redis
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate, save_evidence
from test_executor import add_case

from app.agents.errors import AgentError
from app.agents.registry import RuleQuery
from app.agents.runtime import AgentRuntime
from app.api.booking import get_booking_service
from app.api.faults import get_fault_service
from app.core.config import Settings
from app.db.seeds import seed_database
from app.evidence.reader import TraceReader
from app.evidence.recorder import EvidenceRecorder
from app.executors.budget import FAULT_AUTHORIZATION
from app.executors.control import ExecutionControl, database_now
from app.executors.runner import run_execution
from app.executors.service import RunService
from app.faults.engine import FaultController
from app.faults.service import FaultService
from app.main import create_app
from app.models import FaultProfile, TestExecution, TestRun
from app.schemas.faults import STAGES, FaultSpec
from app.schemas.runs import CreateRun, ExecutionInput
from app.services.booking import BookingService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AC_INTEGRATION") != "1",
        reason="Real MySQL/Redis; provider is an explicit offline double",
    ),
]


def spec(kind="F01", **changes):
    return FaultSpec.model_validate(
        {
            "profile_key": "T07-" + uuid4().hex.upper(),
            "fault_type": kind,
            "trigger_stage": STAGES[kind],
            "target_tool": "retrieve_rules" if kind == "F03" else "confirm_booking",
            **changes,
        }
    )


@pytest.fixture(scope="module")
def infra():
    settings = Settings().model_copy(
        update={
            "executor_allow_paid_model_calls": True,
            "executor_budget_authorization_id": FAULT_AUTHORIZATION,
            "executor_agent_budget_unlimited": True,
            "executor_agent_budget_usd": 0,
            "run_concurrency": 2,
            "executor_namespace": "ac:t07:test:" + uuid4().hex,
        }
    )
    redis = Redis.from_url(settings.redis_url)
    with disposable_database("T07") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        runtime = AgentRuntime(engine, redis, settings, auto_register=True)
        service, reader = RunService(engine, settings), TraceReader(engine, settings)
        yield service, reader, redis, settings, runtime.version_id
        with Session(engine) as session:
            for e in session.scalars(select(TestExecution)):
                runtime.saver.delete_thread(e.session_id)
        keys = list(redis.scan_iter(match=settings.executor_namespace + "*"))
        if keys:
            redis.delete(*keys)
    redis.close()


@pytest.fixture
def make(infra):
    service, reader, redis, settings, version = infra

    def create(profile=None, turns=None, *, assertions=None, **extra):
        identifier = service.create(
            1,
            CreateRun(
                request_id=uuid4(),
                target_version_id=version,
                case_revision_ids=[add_case(service.engine, turns, assertions=assertions)],
                fault_profile_id=profile["id"] if profile else None,
                **extra,
            ),
        )
        return identifier, service.read(1, identifier)["executions"][0]["execution_id"]

    yield create
    with Session(service.engine) as session:
        pending = list(
            session.scalars(
                select(TestRun.run_uuid).where(TestRun.status.in_(("queued", "running")))
            )
        )
    for run in pending:
        service.cancel(1, run)


def events(reader, run):
    found, after = [], None
    while True:
        page = reader.page(1, run, after=after)
        found += page["items"]
        if not page["has_more"]:
            return [f["event"] for f in found]
        after = page["next_cursor"]


@pytest.mark.parametrize("kind", [None, "F01", "F02", "F03", "F04"])
def test_real_tool_matrix_control_vs_four_effects(infra, make, kind):
    service, reader, redis, settings, _ = infra
    profile = FaultService(service.engine).create(spec(kind)) if kind else None
    gateway = OfflineGateway()
    text = gateway.preview()
    if kind == "F03":
        gateway.queue.insert(2, tool("retrieve_rules", {"query": "人数容量时间确认规则"}))
    run, execution = make(profile, [{"user": text}])
    run_execution(service.engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    waiting = service.read(1, run)["executions"][0]
    assert waiting["status"] == "waiting_input", waiting
    draft = waiting["output"]["pending_draft"]
    gateway.confirm(waiting["output"])
    if kind == "F04":
        gateway.queue.insert(
            1,
            tool(
                "lookup_booking_by_request", {"idempotency_key": draft["suggested_idempotency_key"]}
            ),
        )
    service.supply_input(
        1,
        run,
        execution,
        ExecutionInput(
            request_id=uuid4(), expected_revision=waiting["timing"]["revision"], text="确认"
        ),
    )
    run_execution(service.engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    done = service.read(1, run)["executions"][0]
    facts = events(reader, run)
    injected = [f for f in facts if f["type"] == "fault.injected"]
    assert len(injected) == int(kind is not None)
    assert len(done["after"]) == int(kind not in ("F01", "F02")), done
    # The older fixture declares booking_count=0. T08 now evaluates that frozen
    # expectation, so the actual single booking correctly fails it.
    assert done["status"] == ("waiting_input" if kind in ("F01", "F02") else "failed")
    if kind:
        fact = injected[0]
        payload = fact["payload"]
        assert done["timing"]["faults"]["injected"] == 1
        requested = next(f for f in facts if f["event_id"] == fact["parent_event_id"])
        assert (
            requested["type"] == "tool.requested"
            and requested["tool_call_id"] == fact["tool_call_id"]
        )
        returned = next(
            f
            for f in facts
            if f["type"] in ("tool.completed", "tool.error")
            and f["payload"].get("fault_event_id") == fact["event_id"]
        )
        assert returned["payload"]["visible_result"] == payload["visible_result"]
        if kind == "F01":
            assert payload["raw_result"] is None and not payload["dispatched"]
        if kind == "F02":
            assert payload["dispatched"] and len(payload["rollback_probe"]) == 1
            assert payload["after"] == payload["before"] == []
            assert payload["visible_result"]["http_status"] == 503
        if kind == "F03":
            assert isinstance(payload["raw_result"], dict)
            with pytest.raises(json.JSONDecodeError):
                json.loads(payload["visible_result"])
            assert any(
                isinstance(m, ToolMessage) and m.content == payload["visible_result"]
                for call in gateway.calls
                for m in call
            )
            for call in gateway.calls:
                if (
                    isinstance(call[-1], ToolMessage)
                    and call[-1].content == payload["visible_result"]
                ):
                    assert '"retrieval_result": null' in call[0].content
        if kind == "F04":
            assert payload["raw_result"]["commit_state"] == "committed"
            assert payload["visible_result"]["commit_state"] == "unknown"
            assert len(payload["after"]) == 1
            assert any(
                f["payload"].get("tool") == "lookup_booking_by_request"
                and f["payload"].get("arguments", {}).get("idempotency_key")
                == draft["suggested_idempotency_key"]
                for f in facts
                if f["type"] == "tool.requested"
            )
            booking_id = payload["raw_result"]["data"]["booking_id"]
            for call in gateway.calls:
                if (
                    isinstance(call[-1], ToolMessage)
                    and '"code": "response_lost"' in call[-1].content
                ):
                    assert booking_id not in call[0].content
    save_evidence(
        f"matrix-{kind or 'control'}.json",
        {
            "execution": done,
            "events": facts,
            "validation": "Offline model, real tools/MySQL/Redis; no agent verdict",
        },
        task="T07",
    )


@pytest.mark.parametrize(
    "kind,change",
    [
        ("F01", {"trigger_stage": "POST_COMMIT"}),
        ("F02", {"target_tool": "query_availability"}),
        ("F03", {"target_tool": "confirm_booking"}),
        ("F03", {"target_tool": "preview_booking"}),
        ("F04", {"target_tool": "retrieve_rules"}),
        ("F04", {"parameters": {"delay_ms": 1}}),
        ("F01", {"max_triggers": 0}),
        ("F01", {"parameters": {"trigger_window": 0}}),
        ("F01", {"parameters": {"script": "arbitrary"}}),
        ("F01", {"target_tool": "arbitrary"}),
    ],
)
def test_fault_configuration_rejects_unsafe_or_unsupported_values(kind, change):
    with pytest.raises(ValidationError):
        spec(kind, **change)


def test_profile_api_auth_idempotent_revision_and_immutable_storage(infra, make):
    service, _, _, _, _ = infra
    profiles = FaultService(service.engine)
    app = create_app()
    app.dependency_overrides[get_fault_service] = lambda: profiles
    app.dependency_overrides[get_booking_service] = lambda: BookingService(service.engine)
    client = TestClient(app)
    body = spec().model_dump(mode="json")
    assert client.post("/api/v1/fault-profiles", json=body).status_code == 401
    client.auth = ("demo_operator", TEST_PASSWORD)
    response = client.post("/api/v1/fault-profiles", json=body)
    assert response.status_code == 201
    profile = response.json()["data"]
    assert client.post("/api/v1/fault-profiles", json=body).json()["data"] == profile
    assert (
        client.post("/api/v1/fault-profiles", json={**body, "max_triggers": 2}).status_code == 409
    )
    assert client.get("/api/v1/fault-profiles").json()["data"]["items"]
    assert client.get(f"/api/v1/fault-profiles/{profile['id']}").json()["data"] == profile
    with pytest.raises(DBAPIError), Session(service.engine) as session, session.begin():
        session.execute(
            update(FaultProfile).where(FaultProfile.id == profile["id"]).values(max_triggers=2)
        )
    disabled = profiles.create(spec(enabled_for_test=False))
    with pytest.raises(AgentError) as error:
        make(disabled)
    assert error.value.code == "fault_profile_disabled"


def test_persistent_counts_receipts_seed_and_run_isolation(infra, make):
    service, reader, _, settings, _ = infra
    profile = FaultService(service.engine).create(
        spec(target_tool="retrieve_rules", parameters={"trigger_on_nth": 2}, max_triggers=1)
    )
    run, execution = make(profile)
    control_run, _ = make()
    claim = service.claim(execution)
    control = ExecutionControl(service.engine, claim["id"], claim["token"])
    recorder = EvidenceRecorder(control, settings, claim)
    controller = FaultController(control, recorder, claim["manifest"]["fault_profile"])
    state = {
        "scope_id": claim["scope_id"],
        "session_id": claim["session_id"],
        "actor_key": "user:1",
    }
    calls = []

    def invoke(message, tool_id):
        parent = recorder.emit(
            "tool.requested",
            tool="retrieve_rules",
            arguments={"query": "规则"},
            tool_call_id=tool_id,
        )
        return controller.invoke(
            state,
            "retrieve_rules",
            RuleQuery(query="规则"),
            lambda: calls.append(True) or {"chunks": []},
            tool_call_id=tool_id,
            message_id=message,
            parent=parent,
        )

    invoke("message1", "call1")
    first = invoke("message2", "call2")
    with Session(service.engine) as session, session.begin():
        session.get(TestExecution, claim["id"]).lease_until = database_now(session) - timedelta(
            seconds=1
        )
    renewed = service.claim(execution)
    assert renewed["attempt"] == 2
    control = ExecutionControl(service.engine, renewed["id"], renewed["token"])
    recorder = EvidenceRecorder(control, settings, renewed)
    controller = FaultController(control, recorder, renewed["manifest"]["fault_profile"])
    repeated = invoke("message2", "call2")
    invoke("message3", "call3")
    assert len(calls) == 2 and repeated["receipt_reused"]
    assert first["visible_result"] == repeated["visible_result"]
    e = service.read(1, run)["executions"][0]
    assert e["timing"]["faults"]["eligible_calls"] == 3 and e["timing"]["faults"]["injected"] == 1
    assert not any(f["type"].startswith("fault.") for f in events(reader, control_run))
    service.cancel(1, run)
    with pytest.raises(AgentError):
        invoke("message4", "call4")


def test_unresolved_injection_cannot_blindly_reexecute_and_failed_original_not_claimed(infra, make):
    service, reader, _, settings, _ = infra
    profile = FaultService(service.engine).create(spec("F04"))
    run, execution = make(profile)
    claim = service.claim(execution)
    control = ExecutionControl(service.engine, claim["id"], claim["token"])
    recorder = EvidenceRecorder(control, settings, claim)
    controller = FaultController(control, recorder, claim["manifest"]["fault_profile"])
    state = {
        "scope_id": claim["scope_id"],
        "session_id": claim["session_id"],
        "actor_key": "user:1",
    }
    from app.schemas.booking import ConfirmBookingToolInput

    values = ConfirmBookingToolInput(draft_id=uuid4(), draft_hash="a" * 64, idempotency_key=uuid4())
    parent = recorder.emit(
        "tool.requested", tool="confirm_booking", arguments=values.model_dump(mode="json")
    )
    count = []

    def interrupted():
        count.append(True)
        raise RuntimeError("explicit offline interruption before return")

    for expected in (RuntimeError, AgentError):
        with pytest.raises(expected):
            controller.invoke(
                state,
                "confirm_booking",
                values,
                interrupted,
                tool_call_id="call1",
                message_id="message1",
                parent=parent,
            )
    assert len(count) == 1
    assert [f["type"] for f in events(reader, run)].count("fault.injected") == 0
    service.cancel(1, run)
    other, other_execution = make(profile)
    claim = service.claim(other_execution)
    control = ExecutionControl(service.engine, claim["id"], claim["token"])
    recorder = EvidenceRecorder(control, settings, claim)
    controller = FaultController(control, recorder, claim["manifest"]["fault_profile"])
    state.update(scope_id=claim["scope_id"], session_id=claim["session_id"])
    parent = recorder.emit(
        "tool.requested", tool="confirm_booking", arguments=values.model_dump(mode="json")
    )
    result = controller.invoke(
        state,
        "confirm_booking",
        values,
        lambda: {"status": "error", "code": "original_failure", "commit_state": "not_committed"},
        tool_call_id="call-other",
        message_id="message-other",
        parent=parent,
    )
    assert result["visible_result"]["code"] == "original_failure"
    assert [f["type"] for f in events(reader, other)].count("fault.not_applied") == 1
    assert service.read(1, other)["executions"][0]["timing"]["faults"]["injected"] == 0


def test_seeded_window_repeats_across_independent_executions(infra, make):
    service, _, _, settings, _ = infra
    profile = FaultService(service.engine).create(
        spec(
            target_tool="retrieve_rules",
            parameters={"trigger_on_nth": 1, "trigger_window": 4},
            seed=918,
            max_triggers=2,
        )
    )
    observed = []
    for seed in (7, 7, 8):
        run, execution = make(profile, seed=seed)
        claim = service.claim(execution)
        control = ExecutionControl(service.engine, claim["id"], claim["token"])
        recorder = EvidenceRecorder(control, settings, claim)
        controller = FaultController(control, recorder, claim["manifest"]["fault_profile"])
        state = {
            "scope_id": claim["scope_id"],
            "session_id": claim["session_id"],
            "actor_key": "user:1",
        }
        selected = []
        for n in range(1, 7):
            parent = recorder.emit(
                "tool.requested", tool="retrieve_rules", arguments={"query": "规则"}
            )
            result = controller.invoke(
                state,
                "retrieve_rules",
                RuleQuery(query="规则"),
                lambda: {"chunks": []},
                tool_call_id=f"call{n}",
                message_id=f"message{n}",
                parent=parent,
            )
            if result.get("fault_event_id"):
                selected.append(n)
        assert len(selected) == 2
        observed.append(selected)
        service.finish(claim["id"], claim["token"], "inconclusive")
    assert observed[0] == observed[1]
