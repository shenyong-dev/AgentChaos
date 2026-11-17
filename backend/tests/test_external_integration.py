"""Isolated MySQL + real loopback HTTP, platform-authored free protocol example only."""
# ruff: noqa: F811

import os
import threading
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_external_agents import remote, send_data  # noqa: F401
from test_reports import lab  # noqa: F401

from app.agents.errors import AgentError
from app.executors.control import database_now
from app.executors.runner import run_execution
from app.external.cases import CaseFile
from app.external.receipts import cancel_remote, reconcile
from app.external.runner import Calls
from app.external.service import ExternalService
from app.models import Target, TestExecution, User
from app.reports.service import ReportService
from app.schemas.reports import ExportReport
from app.schemas.runs import CreateRun, RecoverExecution
from app.workbench.service import WorkbenchService
from examples.external_agent.server import Store

pytestmark = pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="isolated MySQL/Redis")


def setup(lab, remote, *, turns=None):
    engine, redis, original, *_ = lab
    settings = original.model_copy(
        update={
            "external_agent_registry_path": remote[1].external_agent_registry_path,
            "external_agent_calls_enabled": True,
            "app_env": "test",
            "mysql_database": engine.url.database,
        }
    )
    service = ExternalService(engine, settings)
    checked = service.inspect(1, "example")
    registered = service.register(1, "example", checked["config_hash"])
    case = CaseFile.model_validate(
        {
            "target_kind": "external_http:example",
            "case": {
                "case_key": "EXT-" + uuid4().hex[:12].upper(),
                "revision": 1,
                "family_key": "p06-development-mechanical-control",
                "category": "normal",
                "dataset_split": "dev",
                "reference_clock": "2026-11-16T12:00:00+08:00",
                "turns": turns or [{"user": "你好"}],
                "assertions": [{"type": "reply_literals", "contains": ["收到"], "excludes": []}],
            },
        }
    )
    imported = service.import_case(1, case)
    with Session(engine) as session:
        reviewer = session.scalar(select(User.id).where(User.role == "reviewer"))
    return engine, redis, settings, service, registered, imported, reviewer


def start(lab, values, *, approve=True):
    from app.executors.service import RunService

    engine, _, settings, service, registered, imported, reviewer = values
    if approve:
        service.review(reviewer, imported["id"], imported["content_hash"], "approved")
    runs = RunService(engine, settings)
    run_id = runs.create(
        1,
        CreateRun(
            request_id=uuid4(),
            target_version_id=registered["version_id"],
            case_revision_ids=[imported["id"]],
        ),
    )
    execution = runs.read(1, run_id)["executions"][0]["execution_id"]
    return runs, run_id, execution


def test_registration_review_run_evidence_and_three_format_export(lab, remote):
    values = setup(lab, remote)
    engine, redis, settings, service, registered, imported, _ = values
    with pytest.raises(AgentError):
        start(lab, values, approve=False)
    with pytest.raises(AgentError):
        service.review(1, imported["id"], imported["content_hash"], "approved")
    runs, run_id, execution = start(lab, values)
    assert run_execution(engine, redis, settings, execution)["status"] == "passed"
    result = runs.read(1, run_id)
    item = result["executions"][0]
    assert item["output"]["assistant_text"] == "收到：你好"
    assert item["timing"]["gateway_mode"] == "external_http"
    assert item["after"]["status"] == "not_applicable"
    assert item["usage"]["roles"] == {} and item["usage"]["tickets"] == {}
    versions = WorkbenchService(engine, settings).versions(1, limit=50)["items"]
    assert next(v for v in versions if v["id"] == registered["version_id"])["compatible"]
    rows = WorkbenchService(engine, settings).cases(1, limit=50)["items"]
    assert (
        next(c for c in rows if c["id"] == imported["id"])["adapter_type"]
        == "external_http:example"
    )
    with pytest.raises(AgentError):
        runs.read(999999, run_id)
    report = ReportService(engine, settings).read(1, run_id)
    assert report["knowledge"] == []
    assert {r["metric"] for r in report["executions"][0]["assertions"]} == {
        "evidence_complete",
        "reply_literals",
    }
    exports = ReportService(engine, settings).export(1, run_id, ExportReport(request_id=uuid4()))
    assert len(exports["items"]) == 3


def test_unknown_receipt_is_reconciled_without_recovery_or_second_execute(lab, remote):
    values = setup(lab, remote)
    engine, redis, settings, *_ = values
    runs, run_id, execution = start(lab, values)
    remote[2]["drop"] = True
    assert run_execution(engine, redis, settings, execution)["code"] == "external_result_unknown"
    item = runs.read(1, run_id)["executions"][0]
    assert any(i["state"] == "unknown" for i in item["timing"]["external_intents"].values())
    with pytest.raises(AgentError):
        runs.recover(
            1,
            run_id,
            execution,
            RecoverExecution(request_id=uuid4(), expected_revision=item["timing"]["revision"]),
        )
    receipt = reconcile(engine, settings, 1, run_id, execution)
    assert receipt["inputs_resent"] == 0 and any(
        i["status"] == "received" for i in receipt["items"]
    )
    assert remote[2]["operations"].count("execute") == 1
    assert runs.read(1, run_id)["executions"][0]["status"] == "error"
    assert reconcile(engine, settings, 1, run_id, execution)["items"] == []


def test_crash_after_receipt_before_cursor_reuses_saved_receipt(lab, remote, monkeypatch):
    values = setup(lab, remote)
    engine, redis, settings, *_ = values
    runs, run_id, execution = start(lab, values)
    original = Calls.record

    def record_then_crash(self, request, response=None):
        original(self, request, response)
        if request["operation"] == "execute" and response is not None:
            raise SystemExit("mechanical crash after durable receipt")

    monkeypatch.setattr(Calls, "record", record_then_crash)
    with pytest.raises(SystemExit):
        run_execution(engine, redis, settings, execution)
    monkeypatch.setattr(Calls, "record", original)
    with Session(engine) as session, session.begin():
        row = session.scalar(
            select(TestExecution).where(TestExecution.execution_uuid == execution).with_for_update()
        )
        row.lease_until = database_now(session) - timedelta(seconds=1)
    assert run_execution(engine, redis, settings, execution)["status"] == "passed"
    assert remote[2]["operations"].count("execute") == 1
    assert runs.read(1, run_id)["executions"][0]["attempt"] == 2


def test_recovery_after_cursor_commit_keeps_answer(lab, remote, monkeypatch):
    from app.executors.service import RunService

    values = setup(lab, remote)
    engine, redis, settings, *_ = values
    runs, run_id, execution = start(lab, values)
    with monkeypatch.context() as patch:

        def crash_before_finish(*args, **kwargs):
            raise SystemExit("fixture stops after output commit")

        patch.setattr(RunService, "finish", crash_before_finish)
        with pytest.raises(SystemExit):
            run_execution(engine, redis, settings, execution)
    with Session(engine) as session, session.begin():
        row = session.scalar(
            select(TestExecution).where(TestExecution.execution_uuid == execution).with_for_update()
        )
        assert row.timing_json["cursor"] == 1
        assert row.final_output_json["assistant_text"] == "收到：你好"
        row.lease_until = database_now(session) - timedelta(seconds=1)
    operations = list(remote[2]["operations"])
    assert run_execution(engine, redis, settings, execution)["status"] == "passed"
    assert remote[2]["operations"] == operations
    assert runs.read(1, run_id)["executions"][0]["output"]["assistant_text"] == "收到：你好"


@pytest.mark.parametrize("cancel_at", [0, 1])
def test_cancelled_execution_preserves_late_receipt_and_sends_no_next_turn(
    lab, remote, monkeypatch, cancel_at
):
    values = setup(lab, remote, turns=[{"user": "第一句"}, {"user": "第二句"}, {"user": "第三句"}])
    engine, redis, settings, *_ = values
    runs, run_id, execution = start(lab, values)
    entered, release = threading.Event(), threading.Event()
    original = Store.handle
    active_ids, cancelled_ids = [], []

    def blocked(self, envelope):
        result = original(self, envelope)
        if envelope["operation"] == "cancel":
            cancelled_ids.append(envelope["data"]["original_request_id"])
        if (
            envelope["operation"] == "execute"
            and envelope["data"]["expected_revision"] == cancel_at
        ):
            active_ids.append(envelope["request_id"])
            entered.set()
            release.wait(5)
        return result

    monkeypatch.setattr(Store, "handle", blocked)
    results = []
    thread = threading.Thread(
        target=lambda: results.append(run_execution(engine, redis, settings, execution))
    )
    thread.start()
    try:
        assert entered.wait(5)
        runs.cancel(1, run_id)
        cancel_remote(engine, settings, 1, run_id)
        cancel_remote(engine, settings, 1, run_id)
    finally:
        release.set()
        thread.join(8)
    assert not thread.is_alive()
    item = runs.read(1, run_id)["executions"][0]
    assert item["status"] == "cancelled"
    receipts = list(item["timing"]["external_intents"].values())
    assert any(i["state"] == "received" and i.get("late") for i in receipts)
    assert remote[2]["operations"].count("execute") == cancel_at + 1
    assert remote[2]["operations"].count("cancel") == 1
    assert cancelled_ids == active_ids


def test_registry_drift_disables_new_runs_without_rewriting_old_version(lab, remote):
    values = setup(lab, remote)
    engine, _, settings, service, registered, imported, reviewer = values
    import json
    from pathlib import Path

    from app.executors.service import RunService

    service.review(reviewer, imported["id"], imported["content_hash"], "approved")
    with Session(engine) as session:
        target = session.get(Target, registered["target_id"])
        original = deepcopy(target.capabilities_json)
    path = Path(settings.external_agent_registry_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["targets"][0]["card"]["revision"] = "changed"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(AgentError):
        RunService(engine, settings).create(
            1,
            CreateRun(
                request_id=uuid4(),
                target_version_id=registered["version_id"],
                case_revision_ids=[imported["id"]],
            ),
        )
    with Session(engine) as session:
        assert session.get(Target, registered["target_id"]).capabilities_json == original


def test_authenticated_registration_and_case_owner_gates(lab, remote):
    from fastapi.testclient import TestClient
    from test_database import TEST_PASSWORD

    from app.api.cases import get_case_service
    from app.api.external_agents import get_external_service
    from app.cases.service import CaseService
    from app.executors.service import RunService
    from app.main import create_app

    engine, redis, settings, service, _, imported, reviewer = setup(lab, remote)
    service.review(reviewer, imported["id"], imported["content_hash"], "approved")
    with Session(engine) as session:
        other = session.scalar(select(User.id).where(User.username == "t15_other"))
    other_version = service.register(
        other, "example", service.inspect(other, "example")["config_hash"]
    )
    workbench = WorkbenchService(engine, settings)
    assert imported["id"] not in {row["id"] for row in workbench.cases(other, limit=50)["items"]}
    with pytest.raises(AgentError) as hidden:
        workbench.case(other, imported["id"])
    assert hidden.value.status_code == 404
    with pytest.raises(AgentError) as denied:
        RunService(engine, settings).create(
            other,
            CreateRun(
                request_id=uuid4(),
                target_version_id=other_version["version_id"],
                case_revision_ids=[imported["id"]],
            ),
        )
    assert denied.value.status_code == 404
    app = create_app()
    app.dependency_overrides[get_case_service] = lambda: CaseService(engine, redis, settings)
    app.dependency_overrides[get_external_service] = lambda: service
    with TestClient(app) as client:
        path = "/api/v1/external-agents"
        auth = ("demo_operator", TEST_PASSWORD)
        assert client.get(path).status_code == 401
        checked = client.get(path + "/check?registered_id=example", auth=auth)
        assert checked.status_code == 200
        data = {
            "registered_id": "example",
            "confirmed": True,
            "expected_config_hash": checked.json()["data"]["config_hash"],
        }
        assert (
            client.post(
                path + "/register",
                auth=auth,
                json={**data, "endpoint": "https://unregistered.invalid"},
            ).status_code
            == 400
        )
        registered = client.post(path + "/register", auth=auth, json=data)
        assert registered.status_code == 200 and not registered.json()["data"]["created"]


def test_external_allowance_is_conservative_and_separate_from_native_ledger(
    lab, remote, monkeypatch
):
    import json
    from pathlib import Path

    from examples.external_agent.server import CARD

    card = {**CARD, "billing": "external_account"}
    path = Path(remote[1].external_agent_registry_path)
    registry = json.loads(path.read_text(encoding="utf-8"))
    registry["targets"][0].update(card=card, cost_upper_microusd=60000)
    path.write_text(json.dumps(registry), encoding="utf-8")
    original = Store.handle

    def priced_fixture(self, envelope):
        return card if envelope["operation"] == "capabilities" else original(self, envelope)

    monkeypatch.setattr(Store, "handle", priced_fixture)
    values = setup(lab, remote, turns=[{"user": "第一句"}, {"user": "第二句"}])
    engine, redis, settings, *_ = values
    runs, run_id, execution = start(lab, values)
    assert run_execution(engine, redis, settings, execution)["code"] == "external_budget_exhausted"
    result = runs.read(1, run_id)
    assert result["summary"]["external"] == {"held_upper": 0, "charged_upper": 60000}
    assert result["executions"][0]["usage"]["tickets"] == {}
    assert remote[2]["operations"].count("execute") == 1


def test_preflight_uses_external_connection_and_original_worker_checks(lab, remote):
    import asyncio
    from datetime import UTC, datetime, timedelta

    from app.schemas.readiness import ReadinessCheck, ReadinessReport
    from app.schemas.workflow import RunPlan
    from app.services.readiness import operations
    from app.workbench.workflow import WorkflowService

    values = setup(lab, remote)
    engine, _, settings, service, registered, imported, reviewer = values
    service.review(reviewer, imported["id"], imported["content_hash"], "approved")

    async def observe(_):
        now = datetime.now(UTC)
        checks = [
            ReadinessCheck(
                id=name,
                label=name,
                status="ready"
                if name in {"api", "mysql", "schema", "redis", "worker", "dispatcher"}
                else "blocked",
                code="mechanical_fixture",
                message="isolated readiness fixture",
                next_step="test only",
                checked_at=now,
                expires_at=now + timedelta(seconds=60),
                facts={"observed_mode": "real_provider"} if name == "worker" else {},
            )
            for name in [
                "api",
                "mysql",
                "schema",
                "redis",
                "worker",
                "dispatcher",
                "version",
                "credential",
                "checkpoints",
                "executor_budget",
            ]
        ]
        return ReadinessReport(
            checked_at=now,
            expires_at=now + timedelta(seconds=60),
            checks=checks,
            operations=operations(checks),
        )

    workflow = WorkflowService(engine, settings, readiness=observe)
    plan = RunPlan(
        configuration=CreateRun(
            request_id=uuid4(),
            target_version_id=registered["version_id"],
            case_revision_ids=[imported["id"]],
        )
    )
    result = asyncio.run(workflow.preview(1, plan))
    assert result["ready"] and result["workflow"]["declared_gateway_mode"] == "external_http"
    assert result["mode_label"] == "外部 HTTP 对话服务"
    disabled = WorkflowService(
        engine,
        settings.model_copy(update={"external_agent_calls_enabled": False}),
        readiness=observe,
    )
    blocked = asyncio.run(disabled.preview(1, plan))
    assert blocked["blockers"] == ["external_target"]
