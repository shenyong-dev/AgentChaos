"""Real isolated SQL/Redis and actual execution port, with no model requests."""
# ruff: noqa: F811

import os
from copy import deepcopy
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_executor import add_case
from test_reports import lab  # noqa: F401

from app.adapters.refund import RefundAdapter
from app.adapters.refund_catalog import register
from app.adapters.refund_runner import restore_checkpoint
from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.evaluation.service import load_evidence
from app.executors.runner import run_execution
from app.faults.service import FaultService
from app.models import TestExecution
from app.reliability.service import ReliabilityService
from app.reports.service import ReportService
from app.schemas.faults import FaultSpec
from app.schemas.runs import CreateRun, ExecutionInput
from benchmarks.t18.offline import OfflineGateway

pytestmark = pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="isolated MySQL/Redis")


@pytest.fixture(scope="module")
def target(lab):
    return register(lab[0], lab[2])


def start(lab, target, case=0, fault=None):
    runs = lab[4]
    run = str(uuid4())
    runs.create(
        1,
        CreateRun(
            request_id=run,
            target_version_id=target["version_id"],
            case_revision_ids=[target["case_ids"][case]],
            seed=18002,
            fault_profile_id=fault,
        ),
    )
    execution = runs.read(1, run)["executions"][0]["execution_id"]
    return run, execution


def execute(lab, execution, gateway=None):
    return run_execution(
        lab[0], lab[1], lab[2], execution, gateway_factory=lambda *_: gateway or OfflineGateway()
    )


def test_additional_input_restores_checkpoint_and_original_grant(lab, target):
    profile = FaultService(lab[0]).create(
        FaultSpec(
            profile_key="T18-TEST-F01",
            fault_type="F01",
            trigger_stage="PRE_DISPATCH",
            target_tool="confirm_refund",
            seed=18,
        )
    )["id"]
    run, execution = start(lab, target, case=7, fault=profile)
    assert execute(lab, execution)["status"] == "waiting_input"
    current = lab[4].read(1, run)["executions"][0]
    values = ExecutionInput(
        request_id=uuid4(), expected_revision=current["timing"]["revision"], text="确认退款"
    )
    lab[4].supply_input(1, run, execution, values)
    lab[4].supply_input(1, run, execution, values)  # original request is idempotent
    assert execute(lab, execution)["status"] == "failed"  # original frozen goal is still zero
    report = ReportService(lab[0], lab[2]).read(1, run)
    assert len(report["executions"][0]["after"]) == 1
    with Session(lab[0]) as session:
        row = session.scalar(select(TestExecution).where(TestExecution.execution_uuid == execution))
        bundle = load_evidence(session, lab[4].owned_run(session, 1, run), row, lock_events=False)
    assert any(e["type"] == "checkpoint.restored" for e in bundle.events)
    assert sum(e["type"] == "confirmation.resumed" for e in bundle.events) == 2


def test_malformed_tool_call_is_not_a_successful_empty_answer(lab, target):
    class InvalidGateway:
        def invoke(self, *args, **kwargs):
            return AIMessage(
                content="",
                invalid_tool_calls=[
                    {
                        "name": "confirm_refund",
                        "id": "invalid-1",
                        "args": "{broken",
                        "error": "JSON",
                    }
                ],
            ), {"model_requests": 0}

    run, execution = start(lab, target, case=1)
    assert execute(lab, execution, InvalidGateway())["status"] == "error"
    with Session(lab[0]) as session:
        row = session.scalar(select(TestExecution).where(TestExecution.execution_uuid == execution))
        bundle = load_evidence(session, lab[4].owned_run(session, 1, run), row, lock_events=False)
    assert bundle.after == []
    assert any(
        e["type"] == "tool.error"
        and e["payload"]["visible_result"]["code"] == "tool_contract_rejected"
        for e in bundle.events
    )


def test_cross_domain_and_owner_gates_are_preserved(lab, target):
    booking = add_case(lab[0])
    with pytest.raises(AgentError):
        lab[4].create(
            1,
            CreateRun(
                request_id=uuid4(),
                target_version_id=target["version_id"],
                case_revision_ids=[booking],
                seed=18,
            ),
        )
    run, execution = start(lab, target)
    assert execute(lab, execution)["status"] == "passed"
    with pytest.raises(AgentError) as missing:
        lab[4].read(999999, run)
    assert missing.value.status_code == 404
    with pytest.raises(AgentError) as coverage:
        ReliabilityService(lab[0], lab[2]).read(1, run)
    assert coverage.value.code == "domain_coverage_not_applicable"
    assert ReportService(lab[0], lab[2]).read(1, run)["knowledge"] == []


def test_missing_ledger_is_unknown_and_not_recreated(lab, target):
    run, execution = start(lab, target)
    assert execute(lab, execution)["status"] == "passed"
    with Session(lab[0]) as session:
        row = session.scalar(select(TestExecution).where(TestExecution.execution_uuid == execution))
        store = RefundAdapter(lab[0], lab[2]).store(row.scope_id, 1)
        parked = store.path.with_suffix(".held")
        store.path.rename(parked)
        try:
            assert store.snapshot() is None
            assert not store.path.exists()
        finally:
            parked.rename(store.path)


def test_pending_input_receipt_is_required_for_sql_revision_lead():
    claim = {"owner_id": 1, "scope_id": "scope", "manifest": {"target": {"config_hash": "hash"}}}
    saved = {"cursor": 2, "committed_revision": 4, "owner": 1, "scope": "scope", "binding": "hash"}
    pending = {"request_id": str(uuid4()), "expected_revision": 4, "text": "确认退款"}
    timing = {
        "cursor": 2,
        "revision": 5,
        "pending_input": pending,
        "input_receipts": {pending["request_id"]: canonical_hash(pending)},
    }
    assert restore_checkpoint(saved, timing, claim) is saved
    changed = deepcopy(timing)
    changed["pending_input"]["text"] = "different"
    with pytest.raises(AgentError):
        restore_checkpoint(saved, changed, claim)
