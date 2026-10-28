"""T08 actual SQL/Redis, graph and authenticated APIs. Provider requests remain zero."""

import json
import os
from copy import deepcopy
from uuid import uuid4

import pytest
from agent_test_support import REFERENCE, OfflineGateway
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from redis import Redis
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate
from test_executor import add_case

from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash
from app.api.evaluation import get_evaluation_service
from app.core.config import Settings
from app.db.seeds import seed_database
from app.evaluation.engine import evaluator_manifest
from app.evaluation.service import EvaluationService
from app.evidence.reader import TraceReader
from app.executors.runner import run_execution
from app.executors.service import RunService, case_content
from app.main import create_app
from app.models import Artifact, AssertionResult, TestCase, TestExecution, TestRun, User
from app.schemas.runs import CreateRun, ExecutionInput

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AC_INTEGRATION") != "1", reason="Real MySQL/Redis; no paid calls"
    ),
]


@pytest.fixture(scope="module")
def lab():
    settings = Settings().model_copy(
        update={
            "executor_namespace": "ac:t08:test:" + uuid4().hex,
            "executor_allow_paid_model_calls": False,
        }
    )
    redis = Redis.from_url(settings.redis_url, socket_timeout=15)
    with disposable_database("T08") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        runtime = AgentRuntime(engine, redis, settings, auto_register=True)
        service, evaluation = RunService(engine, settings), EvaluationService(engine, settings)
        yield engine, redis, settings, runtime, service, evaluation
        with Session(engine) as session:
            for e in session.scalars(select(TestExecution)):
                runtime.saver.delete_thread(e.session_id)
        keys = list(redis.scan_iter(match=settings.executor_namespace + "*"))
        if keys:
            redis.delete(*keys)
    redis.close()


def create(lab, *, count=0, text=None, extra=None, legacy=False):
    engine, _, _, runtime, service, _ = lab
    case = add_case(engine, [{"user": text or "你好，不要预约。"}], approved=False)
    with Session(engine) as session, session.begin():
        reviewer_id = session.scalar(select(User.id).where(User.role == "reviewer"))
        row = session.get(TestCase, case)
        row.expected_json = {
            "schema_version": "1.1",
            "assertions": [{"type": "booking_count", "equals": count}] + (extra or []),
        }
        row.content_hash = canonical_hash(case_content(row))
        row.review_status = "approved"
        row.reviewer_id = reviewer_id
        row.reviewed_at = REFERENCE
    identifier = service.create(
        1,
        CreateRun(
            request_id=uuid4(), target_version_id=runtime.version_id, case_revision_ids=[case]
        ),
    )
    execution = service.read(1, identifier)["executions"][0]["execution_id"]
    if legacy:
        # Clone before execution, inserting an immutable historical manifest at creation.
        with Session(engine) as session, session.begin():
            original = session.scalar(select(TestRun).where(TestRun.run_uuid == identifier))
            old_execution = session.scalar(
                select(TestExecution).where(TestExecution.execution_uuid == execution)
            )
            manifest = deepcopy(original.manifest_json)
            manifest.update(trace_schema="1.2", evaluation="T08 not implemented")
            clone = TestRun(
                run_uuid=str(uuid4()),
                created_by=1,
                target_version_id=runtime.version_id,
                manifest_json=manifest,
                manifest_hash=canonical_hash(manifest),
                budget_json=original.budget_json,
                summary_json={},
            )
            session.add(clone)
            session.flush()
            execution = str(uuid4())
            session.add(
                TestExecution(
                    execution_uuid=execution,
                    run_id=clone.id,
                    case_revision_id=case,
                    repeat_no=1,
                    seed=27,
                    scope_id="test:" + execution,
                    session_id=str(uuid4()),
                    input_snapshot_json=old_execution.input_snapshot_json,
                    timing_json={"cursor": 0, "steps": 0, "revision": 0, "dispatch_count": 0},
                    usage_json={"tickets": {}, "roles": {}},
                    attempt_history_json=[],
                )
            )
            identifier = clone.run_uuid
    return identifier, execution


def run_greeting(lab, *, reply="离线测试：没有提交任何预约。", **kwargs):
    identifier, execution = create(lab, **kwargs)
    engine, redis, settings, _, _, _ = lab
    gateway = OfflineGateway()
    gateway.queue.append(AIMessage(content=reply))
    result = run_execution(engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    return identifier, execution, result


def test_auto_evaluation_persists_and_api_is_readonly_paginated_owned(lab):
    engine, _, _, _, service, evaluation = lab
    identifier, execution, result = run_greeting(lab)
    assert result["status"] == "passed", service.read(1, identifier)
    first = evaluation.read(1, execution)
    assert first["summary"]["verdict"] == "passed" and len(first["items"]) == 7
    before = TraceReader(engine, lab[2]).snapshot(1, identifier)["event_count"]
    with Session(engine) as session:
        rows = session.scalar(select(func.count()).select_from(AssertionResult))
    assert evaluation.evaluate(1, execution) == first["summary"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AssertionResult)) == rows
    assert TraceReader(engine, lab[2]).snapshot(1, identifier)["event_count"] == before
    app = create_app()
    app.dependency_overrides[get_evaluation_service] = lambda: evaluation
    from app.api.booking import get_booking_service
    from app.services.booking import BookingService

    app.dependency_overrides[get_booking_service] = lambda: BookingService(engine)
    from app.api.evidence import get_trace_reader

    reader = TraceReader(engine, lab[2])
    app.dependency_overrides[get_trace_reader] = lambda: reader
    with TestClient(app) as client:
        url = f"/api/v1/executions/{execution}/assertions"
        assert client.get(url).status_code == 401
        response = client.get(url + "?limit=2", auth=("demo_operator", TEST_PASSWORD))
        assert response.status_code == 200, response.text
        page = response.json()["data"]
        assert len(page["items"]) == 2 and page["has_more"]
        second = client.get(
            url + f"?cursor={page['next_cursor']}", auth=("demo_operator", TEST_PASSWORD)
        ).json()["data"]
        assert len(second["items"]) == 5
        fact = reader.page(1, identifier)["items"][0]
        single = client.get(
            f"/api/v1/runs/{identifier}/events/{fact['event']['event_id']}",
            auth=("demo_operator", TEST_PASSWORD),
        )
        assert single.status_code == 200 and single.json()["data"] == fact
        assert single.json()["request_id"] == single.headers["x-request-id"]
        assert (
            client.post(
                f"/api/v1/executions/{execution}/evaluate",
                json={"evaluator_version": "fake"},
                auth=("demo_operator", TEST_PASSWORD),
            ).status_code
            == 400
        )
    with pytest.raises(AgentError) as failure:
        evaluation.read(3, execution)
    assert failure.value.status_code == 404
    with pytest.raises(DBAPIError), Session(engine) as session, session.begin():
        session.execute(update(AssertionResult).values(reason="overwrite forbidden"))


def test_failed_independent_expectation_is_not_a_platform_error(lab):
    identifier, execution, result = run_greeting(lab, count=1)
    assert result["status"] == "failed"
    view = lab[4].read(1, identifier)["executions"][0]
    assert view["error"] is None
    row = next(x for x in lab[5].read(1, execution)["items"] if x["key"] == "case:001")
    assert row["verdict"] == "failed" and row["actual"]["active_count"] == 0


def test_waiting_confirmation_is_not_evaluated_until_actual_submission(lab):
    engine, redis, settings, _, service, evaluation = lab
    gateway = OfflineGateway()
    text = gateway.preview()
    identifier, execution = create(lab, count=1, text=text)
    run_execution(engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    waiting = service.read(1, identifier)["executions"][0]
    assert waiting["status"] == "waiting_input"
    assert evaluation.read(1, execution)["items"] == []
    with pytest.raises(AgentError):
        evaluation.evaluate(1, execution)
    gateway.confirm(waiting["output"])
    service.supply_input(
        1,
        identifier,
        execution,
        ExecutionInput(
            request_id=uuid4(), expected_revision=waiting["timing"]["revision"], text="确认"
        ),
    )
    result = run_execution(engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    assert result["status"] == "passed", evaluation.read(1, execution)
    report = evaluation.read(1, execution)
    assert (
        next(x for x in report["items"] if x["key"] == "safety.confirmation")["verdict"] == "passed"
    )
    assert service.read(1, identifier)["executions"][0]["after"][0]["room_code"] == "A101"


def test_large_artifact_is_verified_and_late_damage_appends_error_without_rewriting(lab):
    identifier, execution, result = run_greeting(
        lab,
        reply="没有提交" + "长回复证据" * 10000,
        extra=[{"type": "reply_literals", "contains": ["没有提交"]}],
    )
    assert result["status"] == "passed"
    from app.core.config import PROJECT_ROOT
    from app.evidence.recorder import EVIDENCE_ROOT

    engine, _, settings, _, service, evaluation = lab
    with Session(engine) as session:
        run = service.owned_run(session, 1, identifier)
        artifact = session.scalar(select(Artifact).where(Artifact.run_id == run.id))
        assert artifact is not None
        path = (PROJECT_ROOT / artifact.relative_path).resolve()
        assert path.is_relative_to(EVIDENCE_ROOT.resolve())
        original_bytes = path.read_bytes()
    original = service.read(1, identifier)
    try:
        path.write_bytes(original_bytes + b" ")
        outcome = evaluation.evaluate(1, execution)
    finally:
        path.write_bytes(original_bytes)
    assert outcome["verdict"] == "error"
    assert service.read(1, identifier) == original
    assert len(evaluation.read(1, execution)["versions"]) == 2


def test_legacy_evaluation_appends_results_without_changing_manifest_or_events(lab):
    identifier, execution, result = run_greeting(lab, legacy=True)
    assert result["status"] == "inconclusive"
    service, evaluation = lab[4], lab[5]
    before = service.read(1, identifier)
    reader = TraceReader(lab[0], lab[2])
    event_count = reader.snapshot(1, identifier)["event_count"]
    report = evaluation.evaluate(1, execution)
    assert report["verdict"] == "passed"
    assert service.read(1, identifier) == before
    assert reader.snapshot(1, identifier)["event_count"] == event_count
    assert before["manifest"]["evaluation"] == "T08 not implemented"
    assert evaluator_manifest()["version"] in report["evaluator_version"]


def test_full_event_wrapper_boundary_is_offloaded_before_row_limit(lab):
    from app.core.config import PROJECT_ROOT
    from app.evidence.recorder import PAYLOAD, ROW_LIMIT, append_event

    identifier, _ = create(lab)
    empty = PAYLOAD.validate_python({"kind": "state.changed", "summary": ""}).model_dump(
        mode="json"
    )
    size = len(json.dumps(empty, ensure_ascii=False).encode())
    payload = {"kind": "state.changed", "summary": "x" * (ROW_LIMIT - size - 100)}
    assert (
        len(json.dumps(PAYLOAD.validate_python(payload).model_dump(mode="json")).encode())
        < ROW_LIMIT
    )
    with Session(lab[0]) as session, session.begin():
        run = lab[4].owned_run(session, 1, identifier, lock=True)
        row = append_event(session, run, None, payload, settings=lab[2])
        assert row.payload_json["payload"]["artifact"] is not None
        assert len(json.dumps(row.payload_json, ensure_ascii=False).encode()) <= ROW_LIMIT
        ref = row.payload_json["payload"]["artifact"]
    path, digest = TraceReader(lab[0], lab[2]).artifact(1, identifier, ref["artifact_id"])
    assert (
        digest == ref["sha256"] and json.loads(path.read_bytes())["summary"] == payload["summary"]
    )
    assert path.is_relative_to((PROJECT_ROOT / ".local/evidence").resolve())
