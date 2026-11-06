"""T17K: real isolated SQL/Redis, explicit offline transport; no paid model calls."""

import os
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import pytest
from agent_test_support import REFERENCE, OfflineGateway
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD
from test_executor import add_case
from test_reports import lab as lab

from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.agents.versions import build_manifest, canonical_hash
from app.api.cases import get_case_service
from app.api.reports import get_report_service
from app.cases.service import CaseService
from app.executors.runner import run_execution
from app.executors.service import case_content
from app.main import create_app
from app.models import AssertionResult, TestCase, TestExecution, TestRun
from app.reports.service import ReportService
from app.reports.verification import assess
from app.schemas.reports import DefectReview
from app.schemas.runs import CreateRun

pytestmark = pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="Owned real SQL/Redis")


@pytest.fixture(scope="module")
def pair(lab):
    engine, redis, settings, runtime, runs, _ = lab
    text = OfflineGateway().preview()
    case_id = add_case(engine, [{"user": text}, {"user": "确认"}], approved=False)
    with Session(engine) as session, session.begin():
        case = session.get(TestCase, case_id)
        case.source = "T17K_explicit_offline_control_not_real_model_repair"
        case.expected_json = {
            "schema_version": "1.1",
            "assertions": [
                {"type": "booking_count", "equals": 1},
                {
                    "type": "tool_calls",
                    "tool": "query_availability",
                    "min_calls": 0,
                    "max_calls": 20,
                },
            ],
        }
        case.content_hash = canonical_hash(case_content(case))
        case.review_status, case.reviewer_id, case.reviewed_at = "approved", 2, REFERENCE

    def create(version):
        run = runs.create(
            1, CreateRun(request_id=uuid4(), target_version_id=version, case_revision_ids=[case_id])
        )
        return run, runs.read(1, run)["executions"][0]["execution_id"]

    before, before_execution = create(runtime.version_id)
    bad = OfflineGateway()
    bad.queue.extend([AIMessage(content="明确离线失败控制：未预约。") for _ in range(2)])
    assert (
        run_execution(engine, redis, settings, before_execution, gateway_factory=lambda *_: bad)[
            "status"
        ]
        == "failed"
    )

    def variant(*args, **kwargs):
        manifest = build_manifest(*args, **kwargs)
        manifest["test_control_variant"] = "T17K mechanical version; no real repair claim"
        manifest["config_hash"] = canonical_hash(
            {k: v for k, v in manifest.items() if k != "config_hash"}
        )
        return manifest

    with (
        patch("app.agents.runtime.build_manifest", variant),
        patch("app.executors.service.build_manifest", variant),
    ):
        repaired_runtime = AgentRuntime(engine, redis, settings, auto_register=True)
        after, after_execution = create(repaired_runtime.version_id)

        class RepairGateway(OfflineGateway):
            def invoke(self, *args, **kwargs):
                if not self.queue:
                    with Session(engine) as session:
                        output = session.scalar(
                            select(TestExecution).where(
                                TestExecution.execution_uuid == after_execution
                            )
                        ).final_output_json
                    self.confirm(output)
                return super().invoke(*args, **kwargs)

        good = RepairGateway()
        good.preview()
        assert (
            run_execution(
                engine, redis, settings, after_execution, gateway_factory=lambda *_: good
            )["status"]
            == "passed"
        )
    service = ReportService(engine, settings)
    defect = service.collect(1, before)["items"][0]
    for decision in ("confirmed", "fixed"):
        defect = service.defect(1, defect["defect_key"])
        service.review(
            1,
            defect["defect_key"],
            DefectReview(
                request_id=uuid4(),
                expected_hash=defect["revision_hash"],
                decision=decision,
                fixed_version_id=repaired_runtime.version_id if decision == "fixed" else None,
                note="明确机械控制复核，非真人独立标注或实际模型修复效果。",
                human_attestation=True,
            ),
        )
    return service, service.defect(1, defect["defect_key"]), before, after, after_execution


def test_preview_matches_real_SQL_is_read_only_and_ownership_scoped(lab, pair):
    service, defect, before, after, execution = pair
    original = service.read(1, before)
    frozen = original["executions"][0]["case"]
    assert "arguments" not in frozen["content"]["expected"]["assertions"][1]
    assert frozen["dsl"]["assertions"][1]["arguments"] is None

    def counts():
        with Session(lab[0]) as session:
            return [
                session.scalar(select(func.count()).select_from(model))
                for model in (TestRun, TestExecution, AssertionResult)
            ]

    initial = counts()
    result = service.verification(1, defect["defect_key"], execution, defect["fixed_version_id"])
    assert result["eligible"] and result["comparison"]["conditions_match"]
    assert result["verification"]["gateway_mode"] == "offline_test_double"
    assert service.read(1, after)["executions"][0]["after"]
    assert original == service.read(1, before) and counts() == initial
    assert defect["origin"]["run_id"] == before
    with pytest.raises(AgentError) as error:
        service.verification(3, defect["defect_key"], execution, defect["fixed_version_id"])
    assert error.value.status_code == 404
    with pytest.raises(AgentError) as error:
        service.verification(1, defect["defect_key"], uuid4(), defect["fixed_version_id"])
    assert error.value.status_code == 404


@pytest.mark.parametrize(
    "scenario", ["seed", "waiting", "failed", "missing", "wrong_version", "other_rule"]
)
def test_assessment_exposes_reasons_and_refuses_unverified_repairs(pair, scenario):
    service, defect, before, after, execution = pair
    left, right = service.read(1, before), service.read(1, after)
    peer = right["executions"][0]
    version = defect["fixed_version_id"]
    if scenario == "seed":
        right["run"]["manifest"]["seed"] += 1
    if scenario == "waiting":
        peer["status"] = "waiting_input"
    if scenario == "failed":
        peer["status"] = "failed"
    if scenario == "missing":
        peer["evidence_integrity"]["complete"] = False
    if scenario == "wrong_version":
        version += 100
    if scenario == "other_rule":
        rule = deepcopy(peer["assertions"][-1])
        rule["key"] = "other"
        rule["verdict"] = "failed"
        peer["assertions"].append(rule)
    result = assess(defect, left, right, execution, version)
    assert not result["eligible"] and any(not item["passed"] for item in result["checks"])


def test_stale_saved_verdict_cannot_validate_changed_business_snapshot(lab, pair):
    service, defect, _, _, execution_id = pair
    with Session(lab[0]) as session, session.begin():
        execution = session.scalar(
            select(TestExecution).where(TestExecution.execution_uuid == execution_id)
        )
        original = deepcopy(execution.after_state_json)
        execution.after_state_json = []
    try:
        result = service.verification(
            1, defect["defect_key"], execution_id, defect["fixed_version_id"]
        )
        assert not result["eligible"]
        assert not next(item for item in result["checks"] if item["id"] == "evidence_complete")[
            "passed"
        ]
        with pytest.raises(AgentError) as error:
            service.review(
                1,
                defect["defect_key"],
                DefectReview(
                    request_id=uuid4(),
                    expected_hash=defect["revision_hash"],
                    decision="verified",
                    fixed_version_id=defect["fixed_version_id"],
                    verification_execution_id=execution_id,
                    note="明确陈旧判定负控制，不应保存。",
                    human_attestation=True,
                ),
            )
        assert error.value.code == "verification_mismatch"
    finally:
        with Session(lab[0]) as session, session.begin():
            session.scalar(
                select(TestExecution).where(TestExecution.execution_uuid == execution_id)
            ).after_state_json = original


def test_HTTP_preview_rejects_foreign_identity_and_invalid_parameters(lab, pair):
    service, defect, _, _, execution = pair
    app = create_app()
    app.dependency_overrides[get_case_service] = lambda: CaseService(lab[0], lab[1], lab[2])
    app.dependency_overrides[get_report_service] = lambda: service
    url = f"/api/v1/defects/{defect['defect_key']}/verification"
    params = {"execution_id": execution, "fixed_version_id": defect["fixed_version_id"]}
    with TestClient(app) as client:
        assert client.get(url, params=params, auth=("demo_operator", TEST_PASSWORD)).json()["data"][
            "eligible"
        ]
        assert client.get(url, params=params, auth=("t15_other", TEST_PASSWORD)).status_code == 404
        assert (
            client.get(
                url, params={**params, "fixed_version_id": 0}, auth=("demo_operator", TEST_PASSWORD)
            ).status_code
            == 400
        )


def test_pending_execution_is_explained_without_false_verification(lab, pair):
    service, defect, before, _, _ = pair
    case_id = service.read(1, before)["executions"][0]["case_revision_id"]
    run = lab[4].create(
        1,
        CreateRun(
            request_id=uuid4(), target_version_id=lab[3].version_id, case_revision_ids=[case_id]
        ),
    )
    execution = lab[4].read(1, run)["executions"][0]["execution_id"]
    result = service.verification(1, defect["defect_key"], execution, defect["fixed_version_id"])
    assert not result["eligible"] and result["comparison"] is None
    assert result["checks"] == [
        {"id": "run_completed", "passed": False, "label": "复测运行尚未结束"}
    ]
    lab[4].cancel(1, run)


def test_verified_audit_retains_checks_and_original_evidence_and_retries_same_request(pair):
    service, defect, before, _, execution = pair
    assertions = deepcopy(service.read(1, before)["executions"][0]["assertions"])
    request = DefectReview(
        request_id=uuid4(),
        expected_hash=defect["revision_hash"],
        decision="verified",
        fixed_version_id=defect["fixed_version_id"],
        verification_execution_id=execution,
        note="明确离线机械复核：真实SQL流程验证，非真实Agent修复。",
        human_attestation=True,
    )
    result = service.review(1, defect["defect_key"], request)
    assert result["verification"]["assessment"]["eligible"]
    assert service.review(1, defect["defect_key"], request) == result
    assert len(service.defect(1, defect["defect_key"])["reviews"]) == 3
    assert service.read(1, before)["executions"][0]["assertions"] == assertions
