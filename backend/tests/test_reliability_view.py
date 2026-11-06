"""Actual owned MySQL/Redis evidence; all model transport is an explicit offline control."""

import os
from uuid import uuid4

import pytest
from agent_test_support import OfflineGateway
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate
from test_evaluation import create, run_greeting
from test_executor import add_case

from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.api.booking import get_booking_service
from app.api.evidence import get_trace_reader
from app.core.config import Settings
from app.db.seeds import seed_database
from app.evaluation.service import EvaluationService
from app.evidence.reader import TraceReader
from app.executors.runner import run_execution
from app.executors.service import RunService
from app.faults.service import FaultService
from app.main import create_app
from app.models import AssertionResult, TestExecution, TraceEvent
from app.reliability import service as reliability_module
from app.reliability.service import ReliabilityService
from app.schemas.faults import FaultSpec
from app.schemas.runs import CreateRun
from app.services.booking import BookingService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AC_INTEGRATION") != "1", reason="Real owned SQL/Redis, no paid calls"
    ),
]


@pytest.fixture(scope="module")
def lab():
    settings = Settings().model_copy(
        update={
            "executor_namespace": "ac:t17j:test:" + uuid4().hex,
            "executor_allow_paid_model_calls": False,
        }
    )
    redis = Redis.from_url(settings.redis_url, socket_timeout=15)
    with disposable_database("T17J") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        runtime = AgentRuntime(engine, redis, settings, auto_register=True)
        yield (
            engine,
            redis,
            settings,
            runtime,
            RunService(engine, settings),
            EvaluationService(engine, settings),
        )
        with Session(engine) as session:
            for row in session.scalars(select(TestExecution)):
                runtime.saver.delete_thread(row.session_id)
        keys = list(redis.scan_iter(settings.executor_namespace + "*"))
        if keys:
            redis.delete(*keys)
    redis.close()


def test_summary_reads_real_saved_failure_sql_and_refs_without_appending_evaluations(lab):
    run, execution, _ = run_greeting(lab, count=1)
    engine, _, settings, _, _, _ = lab

    def counts():
        with Session(engine) as session:
            return tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (AssertionResult, TraceEvent)
            )

    before = counts()
    service = ReliabilityService(engine, settings)
    view = service.read(1, run)
    item = view["executions"][0]
    assert item["execution_id"] == execution and item["assessment"] == "failed"
    assert item["business"]["active_bookings"] == 0
    assert item["gateway_mode"] == "offline_test_double"
    assert any(row["verdict"] == "failed" and row["evidence_refs"] for row in item["saved_rules"])
    assert view["cohorts"][0]["statistics"]["reliability"]["denominator"] == 1
    assert service.read(1, run)["executions"] == view["executions"]
    assert counts() == before
    with pytest.raises(AgentError) as failure:
        service.read(3, run)
    assert failure.value.status_code == 404


def test_queued_execution_has_unknown_sql_and_no_successful_coverage(lab):
    run, _ = create(lab)
    view = ReliabilityService(lab[0], lab[2]).read(1, run)
    item = view["executions"][0]
    assert item["assessment"] == "pending" and item["business"]["active_bookings"] is None
    assert view["cohorts"][0]["statistics"]["reliability"]["denominator"] == 0
    assert all(row["denominator"] == 0 for row in view["cohorts"][0]["dimensions"])


def test_a_configured_fault_without_matching_tool_activity_is_not_claimed_as_injected(lab):
    engine, redis, settings, runtime, runs, _ = lab
    profile = FaultService(engine).create(
        FaultSpec(
            profile_key="T17J-NOT-TRIGGERED",
            fault_type="F01",
            trigger_stage="PRE_DISPATCH",
            target_tool="confirm_booking",
        )
    )
    run = runs.create(
        1,
        CreateRun(
            request_id=uuid4(),
            target_version_id=runtime.version_id,
            case_revision_ids=[add_case(engine)],
            fault_profile_id=profile["id"],
        ),
    )
    execution = runs.read(1, run)["executions"][0]["execution_id"]
    gateway = OfflineGateway()
    gateway.queue.append(AIMessage(content="离线控制：未执行预约。"))
    run_execution(engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    item = ReliabilityService(engine, settings).read(1, run)["executions"][0]
    assert item["fault"]["state"] == "not_triggered" and item["fault"]["injected"] == 0
    assert item["evidence_integrity"]["complete"]


def test_old_passed_rule_is_retained_but_not_counted_when_current_evidence_differs(
    lab, monkeypatch
):
    run, _, _ = run_greeting(lab)
    original = reliability_module.load_evidence

    def changed(*args, **kwargs):
        bundle = original(*args, **kwargs)
        bundle.after = [
            {
                "booking_id": "synthetic-read-control",
                "request_key": "same-control",
                "room_id": 1,
                "start_at": "2026-11-05T10:00:00Z",
                "end_at": "2026-11-05T11:00:00Z",
                "attendees": 8,
                "status": "confirmed",
            }
        ]
        return bundle

    monkeypatch.setattr(reliability_module, "load_evidence", changed)
    value = ReliabilityService(lab[0], lab[2]).read(1, run)
    item = value["executions"][0]
    assert item["rule_verdict"] == "passed" and item["business"]["active_bookings"] == 1
    assert item["assessment"] == "insufficient"
    assert item["business"]["task_outcome"] == "unknown"
    assert "saved_evaluation_input_changed" in item["evidence_integrity"]["issues"]
    assert value["cohorts"][0]["statistics"]["reliability"]["denominator"] == 0


def test_api_authentication_ownership_and_response_match_the_read_only_projection(lab):
    run, _, _ = run_greeting(lab)
    app = create_app()
    app.dependency_overrides[get_trace_reader] = lambda: TraceReader(lab[0], lab[2])
    app.dependency_overrides[get_booking_service] = lambda: BookingService(lab[0])
    with TestClient(app) as client:
        path = f"/api/v1/runs/{run}/reliability"
        assert client.get(path).status_code == 401
        response = client.get(path, auth=("demo_operator", TEST_PASSWORD))
        assert response.status_code == 200, response.text
        value = response.json()["data"]
        assert value["schema"] == "reliability-view-v1" and value["complete_read"]
        assert value["executions"][0]["assessment"] == "passed"
        assert response.json()["request_id"] == response.headers["x-request-id"]
        assert client.get(path, auth=("demo_reviewer", TEST_PASSWORD)).status_code == 403
