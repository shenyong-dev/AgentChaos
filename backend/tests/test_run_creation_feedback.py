"""Zero-budget preflight and SQL-derived list outcomes; no provider requests."""

# ruff: noqa: F811
import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_run_workflow import context  # noqa: F401

from app.agents.errors import AgentError
from app.executors.service import RunService
from app.models import TestExecution, TestRun
from app.schemas.runs import RoleBudgets
from app.schemas.workflow import CommitPlan, RunPlan
from app.workbench.service import WorkbenchService
from app.workbench.workflow import WorkflowService

pytestmark = pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="isolated SQL/Redis")


def test_zero_real_budget_rejected_for_ready_and_queue_without_rows(context):
    engine, settings, offline, config, state = context
    real = WorkflowService(engine, settings, readiness=offline.readiness)
    for dispatch in ("ready", "queue_only"):
        with pytest.raises(AgentError) as rejected:
            asyncio.run(real.preview(1, RunPlan(configuration=config, dispatch=dispatch)))
        assert rejected.value.code == "run_budget_required"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(TestRun)) == 0
        assert session.scalar(select(func.count()).select_from(TestExecution)) == 0


def test_positive_budget_preflight_and_old_zero_source_preservation(context):
    engine, settings, offline, config, state = context
    old_plan = asyncio.run(offline.preview(1, RunPlan(configuration=config)))
    receipt = CommitPlan(request_id=old_plan["request_id"], plan_id=old_plan["plan_id"])
    old = asyncio.run(offline.commit(1, receipt))["run_id"]
    settings = settings.model_copy(update={"executor_agent_budget_usd": 1})
    real = WorkflowService(engine, settings, readiness=offline.readiness)
    state["mode"] = "real_provider"
    assert asyncio.run(real.commit(1, receipt)) == {"run_id": old, "receipt_reused": True}
    source = real.source(1, old)
    assert source["configuration"]["budgets"]["agent_microusd"] == 0
    for action in ("rerun", "copy"):
        original = type(config).model_validate(source["configuration"])
        with pytest.raises(AgentError) as rejected:
            asyncio.run(
                real.preview(1, RunPlan(configuration=original, action=action, source_run_id=old))
            )
        assert rejected.value.code == "run_budget_required"
    positive = config.model_copy(
        update={"request_id": uuid4(), "budgets": RoleBudgets(agent_microusd=100_000)}
    )
    plan = asyncio.run(
        real.preview(1, RunPlan(configuration=positive, action="copy", source_run_id=old))
    )
    assert plan["ready"] and plan["configuration"]["budgets"]["agent_microusd"] == 100_000
    assert any(
        diff["field"] == "budgets.agent_microusd" for diff in plan["workflow"]["differences"]
    )
    with Session(engine) as session:
        assert session.get(TestRun, 1).budget_json["agent_microusd"] == 0
        assert session.scalar(select(func.count()).select_from(TestRun)) == 1


def test_run_list_counts_come_from_executions_not_stale_summary(context):
    engine, settings, _, config, _ = context
    identifier = RunService(engine, settings).create(1, config)
    with Session(engine) as session, session.begin():
        run = session.scalar(select(TestRun).where(TestRun.run_uuid == identifier))
        run.status = "completed"
        run.summary_json = {"counts": {"passed": 1}}
        execution = session.scalar(select(TestExecution).where(TestExecution.run_id == run.id))
        execution.status = "error"
    row = WorkbenchService(engine, settings).runs(1)["items"][0]
    assert row["status"] == "completed" and row["summary"]["counts"] == {"passed": 1}
    assert row["execution_counts"] == {"error": 1}
