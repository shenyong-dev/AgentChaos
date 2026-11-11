"""Actual disposable SQL/Redis and original execution path; zero paid requests."""

import os
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from redis import Redis
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash
from app.cases.service import CaseService
from app.core.config import PROJECT_ROOT
from app.executors.runner import run_execution
from app.executors.service import RunService
from app.models import TestExecution, TestRun, User
from app.reductions.service import ReductionService
from app.reports.service import ReportService
from app.schemas.cases import CandidateReview
from app.schemas.reductions import (
    ReductionCreate,
    ReductionExecute,
    ReductionReview,
    ReductionStage,
)
from app.schemas.runs import CreateRun, RoleBudgets
from app.workbench.service import WorkbenchService
from benchmarks.t17l.lab import Lab
from benchmarks.t17l.mutations import variant
from benchmarks.t17l.offline import OfflineGateway
from benchmarks.t17l.runner import add_case

pytestmark = pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="disposable MySQL/Redis")


class Gateway(OfflineGateway):
    def invoke(self, messages, tools, **kwargs):
        latest = next(str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage))
        if latest in {"你好。", "谢谢。"}:
            return AIMessage(content="离线接线控制：你好。"), {
                "call_id": "offline-" + uuid4().hex,
                "model_requests": 0,
                "chunks": [],
                "response": {"model_returned": "offline-test-double"},
            }
        return super().invoke(messages, tools, **kwargs)


@pytest.fixture(scope="module")
def lab():
    output = PROJECT_ROOT / ".local/t17m" / ("free-" + uuid4().hex[:10])
    value = Lab(output, real=False)
    redis = Redis.from_url(value.settings.redis_url, socket_timeout=15)
    try:
        with variant(
            value.engine,
            value.settings,
            "confirmation",
            "mutant",
            {"experiment": "T17M-offline-control"},
        ):
            runtime = AgentRuntime(
                value.engine,
                redis,
                value.settings,
                auto_register=True,
                prefix=value.settings.executor_namespace + ":session",
            )
            yield value, redis, runtime
    finally:
        value.archive()
        redis.close()


def run(lab, identifier):
    value, redis, _ = lab
    service = RunService(value.engine, value.settings)
    execution = service.read(1, identifier)["executions"][0]["execution_id"]
    with patch("app.agents.gateway.remember_success", lambda *_: None):
        run_execution(
            value.engine,
            redis,
            value.settings,
            execution,
            gateway_factory=lambda *_: Gateway("confirmation"),
        )
    return execution


def test_actual_failure_reduction_review_idempotency_and_library_gate(lab):
    value, redis, runtime = lab
    engine, settings = value.engine, value.settings
    group = {
        "id": "reduction",
        "turns": [
            "你好。",
            "2026-11-07，A101，14:00到15:00，8人。仅查看草稿，等我明确确认后才能提交。",
            "拒绝确认，不预约了。",
            "谢谢。",
        ],
        "expected_count": 0,
    }
    case = add_case(engine, group, "2026-11-06T01:00:00+00:00")
    runs = RunService(engine, settings)
    origin = runs.create(
        1,
        CreateRun(
            request_id=uuid4(),
            target_version_id=runtime.version_id,
            case_revision_ids=[case],
            seed=13029,
            budgets=RoleBudgets(agent_microusd=0),
        ),
    )
    run(lab, origin)
    reports = ReportService(engine, settings)
    assert reports.read(1, origin)["executions"][0]["after"][0]["attendees"] == 8
    defect = reports.collect(1, origin)["items"][0]["defect_key"]
    service = ReductionService(engine, settings)
    create = ReductionCreate(request_id=uuid4())
    plan = service.create(1, defect, create)
    assert service.create(1, defect, create)["plan_id"] == plan["plan_id"]
    assert not plan["variants"][1]["verified"]
    with pytest.raises(AgentError, match="人工确认"):
        service.execute(
            1,
            plan["plan_id"],
            1,
            ReductionExecute(
                request_id=uuid4(), expected_hash=plan["revision_hash"], paid_attestation=True
            ),
        )
    with pytest.raises(AgentError, match="三次同签名"):
        service.stage(
            1,
            plan["plan_id"],
            1,
            ReductionStage(request_id=uuid4(), expected_hash=plan["revision_hash"]),
        )
    review = ReductionReview(
        request_id=uuid4(),
        expected_hash=plan["revision_hash"],
        keep=[1, 2],
        semantic_preserved=True,
        human_attestation=True,
        note="离线受控语义确认夹具，非真人实验标签。",
    )
    plan = service.review(1, plan["plan_id"], 1, review)
    assert service.review(1, plan["plan_id"], 1, review)["revision_hash"] == plan["revision_hash"]
    for index in [0, 1]:
        request = ReductionExecute(
            request_id=uuid4(), expected_hash=plan["revision_hash"], paid_attestation=True
        )
        plan = service.execute(1, plan["plan_id"], index, request)
        same = service.execute(1, plan["plan_id"], index, request)
        assert same["variants"][index]["runs"] == plan["variants"][index]["runs"]
        for identifier in plan["variants"][index]["runs"]:
            run(lab, identifier)
        plan = service.read(1, plan["plan_id"])
        assert plan["variants"][index]["verified"]
    private_case = plan["variants"][1]["case_id"]
    with Session(engine) as session:
        assert private_case not in [
            c.id for c in session.scalars(WorkbenchService(engine, settings)._cases(session))
        ]
        reviewer = session.scalar(select(User.id).where(User.role == "reviewer"))
    with pytest.raises(AgentError, match="只能由原提炼计划"):
        runs.create(
            1,
            CreateRun(
                request_id=uuid4(),
                target_version_id=runtime.version_id,
                case_revision_ids=[private_case],
            ),
        )
    stage = ReductionStage(request_id=uuid4(), expected_hash=plan["revision_hash"])
    plan = service.stage(1, plan["plan_id"], 1, stage)
    assert service.stage(1, plan["plan_id"], 1, stage)["draft"] == plan["draft"]
    cases = CaseService(engine, redis, settings)
    batch = cases.read(1, plan["draft"]["batch_id"])
    candidate = batch["candidates"][0]
    assert (
        batch["mode"] == "verified_reduction"
        and candidate["case_revisions"][0]["status"] == "draft"
    )
    assert candidate["recheck"]["status"] == "valid"
    result = cases.review(
        reviewer,
        candidate["id"],
        CandidateReview(
            request_id=uuid4(),
            decision="approve",
            note="离线审核接线控制，不是独立真人标注。",
            expected_candidate_hash=candidate["candidate_hash"],
        ),
    )
    with Session(engine) as session:
        assert result["approved_case_revision_id"] in [
            c.id for c in session.scalars(WorkbenchService(engine, settings)._cases(session))
        ]
        other = User(
            username="other_reduction",
            role="operator",
            enabled=True,
            password_hash=session.get(User, 1).password_hash,
        )
        session.add(other)
        session.commit()
        other_id = other.id
    with pytest.raises(AgentError):
        service.read(other_id, plan["plan_id"])
    assert reports.read(1, origin)["executions"][0]["after"][0]["attendees"] == 8
    with Session(engine) as session:
        executions = list(session.scalars(select(TestExecution)))
        assert len({e.scope_id for e in executions}) == len(executions)
        assert len({e.session_id for e in executions}) == len(executions)
    value.completed_plan = plan
    value.completed_defect = defect
    Path(value.output / "success.txt").write_text(
        "7 actual offline executions; original and candidate each 3/3 same failure. "
        "No provider requests.\n",
        encoding="utf-8",
    )


def test_rejection_partial_journal_recovery_and_database_fence(lab):
    value, _, _ = lab
    engine, settings = value.engine, value.settings
    service = ReductionService(engine, settings)
    plan = service.create(1, value.completed_defect, ReductionCreate(request_id=uuid4()))
    paid_service = ReductionService(
        engine, settings.model_copy(update={"executor_allow_paid_model_calls": True})
    )
    with pytest.raises(AgentError, match="离线控制"):
        paid_service.execute(
            1,
            plan["plan_id"],
            0,
            ReductionExecute(
                request_id=uuid4(), expected_hash=plan["revision_hash"], paid_attestation=True
            ),
        )
    plan = service.review(
        1,
        plan["plan_id"],
        1,
        ReductionReview(
            request_id=uuid4(),
            expected_hash=plan["revision_hash"],
            keep=[0],
            semantic_preserved=False,
            human_attestation=True,
            note="删除了预约需求，明确拒绝。",
        ),
    )
    with pytest.raises(AgentError, match="人工确认"):
        service.execute(
            1,
            plan["plan_id"],
            1,
            ReductionExecute(
                request_id=uuid4(), expected_hash=plan["revision_hash"], paid_attestation=True
            ),
        )
    plan = service.review(
        1,
        plan["plan_id"],
        2,
        ReductionReview(
            request_id=uuid4(),
            expected_hash=plan["revision_hash"],
            keep=[1, 2],
            semantic_preserved=True,
            human_attestation=True,
            note="明确离线语义夹具。",
        ),
    )
    original = service.runs.create
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(args[1].request_id)
        if len(calls) == 2:
            raise RuntimeError("explicit simulated response loss")
        return original(*args, **kwargs)

    request = ReductionExecute(
        request_id=uuid4(), expected_hash=plan["revision_hash"], paid_attestation=True
    )
    with patch.object(service.runs, "create", interrupted), pytest.raises(RuntimeError):
        service.execute(1, plan["plan_id"], 2, request)
    plan = service.read(1, plan["plan_id"])
    ids = plan["variants"][2]["runs"]
    assert (
        len(ids) == 3
        and sum(e.get("read_error") == "run_not_found" for e in plan["variants"][2]["executions"])
        == 2
    )
    resumed = service.execute(
        1,
        plan["plan_id"],
        2,
        ReductionExecute(
            request_id=uuid4(), expected_hash=plan["revision_hash"], paid_attestation=True
        ),
    )
    assert resumed["variants"][2]["runs"] == ids
    assert not any(e.get("read_error") for e in resumed["variants"][2]["executions"])
    with Session(engine) as session:
        base = session.scalar(select(TestRun).where(TestRun.run_uuid == ids[0]))
        manifest = deepcopy(base.manifest_json)
        manifest["workflow"]["reduction_id"] = str(uuid4())
        budget = deepcopy(base.budget_json)
        execution = session.scalar(select(TestExecution).where(TestExecution.run_id == base.id))
        snapshot = deepcopy(execution.input_snapshot_json)
        case_id = execution.case_revision_id
    with pytest.raises(DBAPIError, match="approved_case_required"):
        with Session(engine) as session, session.begin():
            fake = TestRun(
                run_uuid=str(uuid4()),
                created_by=1,
                target_version_id=manifest["target_version_id"],
                manifest_json=manifest,
                manifest_hash=canonical_hash(manifest),
                budget_json=budget,
                summary_json={},
            )
            session.add(fake)
            session.flush()
            session.add(
                TestExecution(
                    execution_uuid=str(uuid4()),
                    run_id=fake.id,
                    case_revision_id=case_id,
                    repeat_no=1,
                    seed=13029,
                    scope_id="test:" + str(uuid4()),
                    session_id=str(uuid4()),
                    input_snapshot_json=snapshot,
                    timing_json={},
                    usage_json={},
                    attempt_history_json=[],
                )
            )
            session.flush()
