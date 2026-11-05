"""Real SQL/Redis plans: stale gates, owner isolation, exact lineage and receipts."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import register_version
from app.core.config import Settings
from app.db.seeds import seed_database
from app.executors.dispatch import dispatch_pending
from app.executors.service import RunService
from app.models import TestCase as Case
from app.models import TestExecution as Execution
from app.models import TestRun as Run
from app.schemas.readiness import OperationReadiness, ReadinessReport
from app.schemas.runs import CreateRun
from app.schemas.workflow import CommitPlan, RunPlan
from app.services.readiness import observation
from app.workbench.workflow import WorkflowService

pytestmark = pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="disposable MySQL")


@pytest.fixture
def context():
    from test_database import TEST_PASSWORD, disposable_database, migrate
    from test_executor import add_case

    with disposable_database("T17H") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        settings = Settings().model_copy(
            update={
                "mysql_database": engine.url.database,
                "executor_namespace": "ac:t17h:workflow-test:" + uuid4().hex,
                "deepseek_api_key": SecretStr(""),
                "allow_paid_model_calls": False,
                "executor_allow_paid_model_calls": False,
                "casegen_allow_paid_model_calls": False,
                "judge_allow_paid_model_calls": False,
                "executor_agent_budget_usd": 0,
                "executor_agent_budget_unlimited": False,
            }
        )
        state = {"worker": True, "mode": "offline_test_double"}

        async def readiness(_):
            checks = [
                observation(
                    k,
                    k,
                    "ready" if k != "worker" or state["worker"] else "blocked",
                    "controlled",
                    "explicit free test observation",
                    "test",
                    observed_mode=state["mode"],
                )
                for k in (
                    "api",
                    "mysql",
                    "schema",
                    "redis",
                    "checkpoints",
                    "version",
                    "credential",
                    "executor_budget",
                    "worker",
                    "dispatcher",
                )
            ]
            now = datetime.now(UTC)
            return ReadinessReport(
                checked_at=now,
                expires_at=now + timedelta(seconds=60),
                checks=checks,
                operations=[
                    OperationReadiness(
                        id="batch",
                        label="batch",
                        status="ready",
                        required_checks=[c.id for c in checks],
                        blockers=[],
                        note="controlled readiness only",
                        destination="/workbench",
                    )
                ],
            )

        service = WorkflowService(engine, settings, readiness=readiness, mode="offline_test_double")
        version = register_version(engine, RunService(engine, settings).current_manifest())
        case = add_case(engine)
        config = CreateRun(
            request_id=uuid4(),
            target_version_id=version,
            case_revision_ids=[case],
            budgets={"agent_microusd": 0},
        )
        try:
            yield engine, settings, service, config, state
        finally:
            with Redis.from_url(settings.redis_url) as client:
                keys = list(client.scan_iter(settings.executor_namespace + ":*"))
                if keys:
                    client.delete(*keys)


def preview(service, config, **kwargs):
    return asyncio.run(service.preview(1, RunPlan(configuration=config, **kwargs)))


def commit(service, plan):
    return asyncio.run(
        service.commit(1, CommitPlan(request_id=plan["request_id"], plan_id=plan["plan_id"]))
    )


def test_preflight_does_not_create_and_offline_worker_cannot_be_assumed(context):
    engine, _, service, config, state = context
    state["worker"] = False
    plan = preview(service, config)
    assert not plan["ready"] and "worker" in plan["blockers"] and plan["plan_id"] is None
    queued = preview(service, config, dispatch="queue_only")
    assert queued["ready"] and queued["mode_label"] == "明确离线受控模型"
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 0
    state["worker"] = True
    state["mode"] = "real_provider"
    assert "worker" in preview(service, config)["blockers"]


def test_commit_rechecks_readiness_and_does_not_replace_the_original_receipt(context):
    engine, _, service, config, state = context
    plan = preview(service, config)
    state["worker"] = False
    with pytest.raises(AgentError, match="未就绪"):
        commit(service, plan)
    state["worker"] = True
    result = commit(service, plan)
    assert not result["receipt_reused"]
    with service.client() as client:
        client.delete(service.key(1, plan["plan_id"]))
    assert commit(service, plan)["receipt_reused"]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 1
        run = db.scalar(select(Run))
        execution = db.scalar(select(Execution))
        assert run.manifest_json["workflow"]["plan_id"] == plan["plan_id"]
        assert execution.timing_json["queue_deadline"] == plan["queue_deadline"]
    with pytest.raises(AgentError):
        asyncio.run(
            service.commit(2, CommitPlan(request_id=config.request_id, plan_id=plan["plan_id"]))
        )


@pytest.mark.parametrize("change", ["expires_at", "environment", "request_id"])
def test_expired_changed_and_wrong_request_plans_do_not_write(context, change):
    engine, _, service, config, _ = context
    plan = preview(service, config)
    with service.client() as client:
        key = service.key(1, plan["plan_id"])
        value = json.loads(client.get(key))
        if change == "expires_at":
            value["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        elif change == "environment":
            value["environment"] = "stale-controlled-environment"
        else:
            value["request"]["configuration"]["request_id"] = str(uuid4())
        client.set(key, json.dumps(value), ex=90)
    with pytest.raises(AgentError):
        commit(service, plan)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 0


def test_source_reproduction_preserves_configuration_and_explicit_copy_diff(context):
    engine, _, service, config, _ = context
    original = commit(service, preview(service, config))["run_id"]
    source = service.source(1, original)
    rerun = CreateRun.model_validate(source["configuration"])
    exact = preview(service, rerun, action="rerun", source_run_id=original)
    assert exact["workflow"]["differences"] == []
    first = commit(service, exact)["run_id"]
    assert first != original
    changed = rerun.model_copy(update={"request_id": uuid4(), "seed": 42})
    with pytest.raises(AgentError, match="原配置"):
        preview(service, changed, action="rerun", source_run_id=original)
    copied = preview(service, changed, action="copy", source_run_id=original)
    assert copied["workflow"]["differences"] == [
        {"field": "seed", "before": config.seed, "after": 42}
    ]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 2
    with pytest.raises(AgentError):
        service.source(2, original)


def test_queue_expiration_finishes_without_dispatch_or_claim(context):
    engine, settings, service, config, _ = context
    result = commit(service, preview(service, config, dispatch="queue_only"))
    with Session(engine) as db, db.begin():
        row = db.scalar(select(Execution))
        row.timing_json = {
            **row.timing_json,
            "queue_deadline": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
        execution = row.execution_uuid
    published = []
    outcome = dispatch_pending(engine, settings, publisher=published.append)
    assert outcome["expired"] == 1 and published == []
    assert RunService(engine, settings).claim(execution) is None
    view = RunService(engine, settings).read(1, result["run_id"])
    assert view["executions"][0]["error"]["code"] == "execution_timeout"


def test_non_development_or_unapproved_case_cannot_be_selected(context):
    from test_executor import add_case

    engine, _, service, config, _ = context
    draft = add_case(engine, approved=False)
    with pytest.raises(AgentError):
        preview(service, config.model_copy(update={"case_revision_ids": [draft]}))
    with Session(engine) as db:
        assert db.get(Case, draft).review_status != "approved"


def test_daily_environment_cannot_enable_the_control_mode():
    with pytest.raises(AgentError, match="离线控制"):
        WorkflowService(None, Settings(_env_file=None), mode="offline_test_double")


def test_incompatible_queued_history_is_neither_dispatched_nor_claimed_by_daily_worker(
    context, monkeypatch
):
    from app.workers import celery_app as worker_module

    engine, settings, _, config, _ = context
    previous = settings.model_copy(
        update={"agent_max_output_tokens": settings.agent_max_output_tokens + 1}
    )
    old_service = RunService(engine, previous)
    old_version = register_version(engine, old_service.current_manifest())
    request = config.model_copy(update={"request_id": uuid4(), "target_version_id": old_version})
    identifier = old_service.create(1, request)
    with Session(engine) as db, db.begin():
        row = db.scalar(
            select(Execution).where(
                Execution.run_id == db.scalar(select(Run.id).where(Run.run_uuid == identifier))
            )
        )
        row.timing_json = {
            **row.timing_json,
            "queue_deadline": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
    original = old_service.read(1, identifier)
    sent = []
    result = dispatch_pending(engine, settings, publisher=sent.append)
    assert sent == [] and result["incompatible"] == 1 and result["expired"] == 0
    monkeypatch.setattr(worker_module, "settings", settings)
    returned = worker_module.execute.run(original["executions"][0]["execution_id"])
    assert returned == {"claimed": False, "reason": "incompatible_configuration"}
    assert old_service.read(1, identifier) == original
