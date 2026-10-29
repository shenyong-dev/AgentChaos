"""T15 reports over real isolated MySQL/Redis/offline executions; no provider calls."""

import hashlib
import io
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zipfile import ZipFile

import pytest
from agent_test_support import REFERENCE, OfflineGateway
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate
from test_evaluation import run_greeting
from test_executor import add_case

from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.agents.versions import build_manifest, canonical_hash
from app.api.cases import get_case_service
from app.api.reports import get_report_service
from app.cases.service import CaseService
from app.core.config import PROJECT_ROOT, Settings
from app.db.seeds import seed_database
from app.evaluation.service import EvaluationService
from app.executors.runner import run_execution
from app.executors.service import RunService, case_content
from app.main import create_app
from app.models import Artifact, AssertionResult, TestCase, TestExecution, TestRun, User
from app.reports.analysis import compare
from app.reports.service import ReportService
from app.schemas.reports import DefectReview, ExportReport
from app.schemas.runs import CreateRun

pytestmark = pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="isolated MySQL/Redis")


@pytest.fixture(scope="module")
def lab():
    settings = Settings().model_copy(
        update={
            "executor_namespace": "ac:t15:test:" + uuid4().hex,
            "allow_paid_model_calls": False,
            "executor_allow_paid_model_calls": False,
        }
    )
    redis = Redis.from_url(settings.redis_url, socket_timeout=15)
    with disposable_database("T15") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        with Session(engine) as session, session.begin():
            owner = session.get(User, 1)
            session.add(
                User(
                    username="t15_other",
                    role="operator",
                    enabled=True,
                    password_hash=owner.password_hash,
                )
            )
        runtime = AgentRuntime(engine, redis, settings, auto_register=True)
        runs = RunService(engine, settings)
        values = (engine, redis, settings, runtime, runs, EvaluationService(engine, settings))
        yield values
        with Session(engine) as session:
            for execution in session.scalars(select(TestExecution)):
                runtime.saver.delete_thread(execution.session_id)
        keys = list(redis.scan_iter(match=settings.executor_namespace + "*"))
        if keys:
            redis.delete(*keys)
    redis.close()


@pytest.fixture(scope="module")
def completed(lab):
    failed, execution, result = run_greeting(
        lab, count=1, reply="明确离线故障控制：声称完成，实际未预约。" + "证据 ````` " * 2500
    )
    assert result["status"] == "failed"
    passed, _, result = run_greeting(lab, count=0)
    assert result["status"] == "passed"
    return failed, execution, passed


def test_report_denominators_original_assertions_and_frozen_knowledge(lab, completed):
    engine, _, settings, _, _, _ = lab
    service = ReportService(engine, settings)
    failed, execution, passed = completed
    report = service.read(1, failed)
    assert report["statistics"]["reliability"] == {
        "numerator": 0,
        "denominator": 1,
        "rate": 0,
        "unit": "execution, latest rule version; repeats are not independent families",
        "exclusions": {},
    }
    assert report["executions"][0]["execution_id"] == execution
    assert report["knowledge"] and all(item["available"] for item in report["knowledge"])
    assert service.read(1, passed)["statistics"]["reliability"]["numerator"] == 1
    assert service.read(1, failed) == report


def test_condition_check_refuses_different_cases_and_retains_zero_denominator(lab, completed):
    service = ReportService(lab[0], lab[2])
    before = service.read(1, completed[0])
    same = compare(before, before)
    assert same["conditions_match"] and not same["formal_v0_v1_experiment"]
    different = service.comparison(1, completed[0], completed[2])
    assert not different["conditions_match"] and different["reliability_rate_delta"] is None
    assert (
        next(item for item in different["checks"] if item["condition"] == "case_hashes")["status"]
        == "mismatch"
    )


def test_candidates_and_review_are_idempotent_separate_from_original_results(lab, completed):
    engine, _, settings, _, _, _ = lab
    service = ReportService(engine, settings)
    failed, _, _ = completed
    original = service.read(1, failed)["executions"][0]["assertions"]
    candidates = service.collect(1, failed)["items"]
    assert candidates and len(service.collect(1, failed)["items"]) == len(candidates)
    candidate = candidates[0]
    request = DefectReview(
        request_id=uuid4(),
        expected_hash=candidate["revision_hash"],
        decision="confirmed",
        note="明确机械控制测试，非团队真人审核或真实Agent质量结论。",
        human_attestation=True,
    )
    result = service.review(1, candidate["defect_key"], request)
    assert result["after"]["status"] == "confirmed"
    assert service.review(1, candidate["defect_key"], request) == result
    assert service.defect(1, candidate["defect_key"])["reviews"] == [result]
    reviewed_report = service.read(1, failed)
    assert reviewed_report["defects"][0]["reviews"] == [result]
    assert service.read(1, failed)["executions"][0]["assertions"] == original
    with pytest.raises(AgentError) as failure:
        service.review(
            1, candidate["defect_key"], request.model_copy(update={"request_id": uuid4()})
        )
    assert failure.value.status_code == 409


def test_json_markdown_zip_hashes_idempotency_and_offline_verifier(lab, completed, tmp_path):
    service = ReportService(lab[0], lab[2])
    request = ExportReport(request_id=uuid4())
    result = service.export(1, completed[0], request)
    assert service.export(1, completed[0], request) == result
    assert len(result["items"]) == 3
    for item in result["items"]:
        payload, _, _, digest = service.download(1, item["artifact_id"])
        assert hashlib.sha256(payload).hexdigest() == digest == item["sha256"]
        if item["kind"] == "report_json":
            assert json.loads(payload)["run"]["run_id"] == completed[0]
        if item["kind"] == "report_bundle":
            with ZipFile(io.BytesIO(payload)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert len(manifest["files"]) >= 8
                assert not any("report_bundle" in name for name in archive.namelist())
                for name, record in manifest["files"].items():
                    assert hashlib.sha256(archive.read(name)).hexdigest() == record["sha256"]
                script = tmp_path / "verify.py"
                script.write_bytes(archive.read("verify.py"))
            bundle = tmp_path / "report.zip"
            bundle.write_bytes(payload)
            import sys

            checked = subprocess.run(
                [sys.executable, str(script), str(bundle)], capture_output=True, text=True
            )
            assert checked.returncode == 0, checked.stderr
    with pytest.raises(AgentError) as failure:
        service.export(1, completed[0], request.model_copy(update={"expires_hours": 1}))
    assert failure.value.status_code == 409


def test_expiry_and_tamper_are_rejected_without_replacing_original_files(lab, completed):
    now = datetime.now(UTC)
    service = ReportService(lab[0], lab[2], clock=lambda: now)
    result = service.export(1, completed[2], ExportReport(request_id=uuid4(), expires_hours=1))
    identifier = result["items"][0]["artifact_id"]
    expired = ReportService(lab[0], lab[2], clock=lambda: now + timedelta(hours=2))
    with pytest.raises(AgentError) as failure:
        expired.download(1, identifier)
    assert failure.value.status_code == 410
    with Session(lab[0]) as session:
        path = PROJECT_ROOT / session.get(Artifact, identifier).relative_path
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"tampered control")
        with pytest.raises(AgentError) as failure:
            service.download(1, identifier)
        assert failure.value.status_code == 503
    finally:
        path.write_bytes(original)


def test_concurrent_export_same_id_materializes_once(lab, completed):
    service = ReportService(lab[0], lab[2])
    request = ExportReport(request_id=uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.export(1, completed[2], request), range(2)))
    assert results[0] == results[1]


def test_owned_http_reads_downloads_and_no_model_dispatch(lab, completed):
    engine, redis, settings, _, _, _ = lab
    service = ReportService(engine, settings)
    app = create_app()
    app.dependency_overrides[get_case_service] = lambda: CaseService(engine, redis, settings)
    app.dependency_overrides[get_report_service] = lambda: service
    with Session(engine) as session:
        counts = (
            session.scalar(select(func.count()).select_from(TestRun)),
            session.scalar(select(func.count()).select_from(AssertionResult)),
        )
    with TestClient(app) as client:
        url = f"/api/v1/reports/runs/{completed[0]}"
        assert client.get(url).status_code == 401
        assert client.get(url, auth=("demo_reviewer", TEST_PASSWORD)).status_code == 403
        assert client.get(url, auth=("t15_other", TEST_PASSWORD)).status_code == 404
        assert client.get(url, auth=("demo_operator", TEST_PASSWORD)).status_code == 200
        exported = client.post(
            url + "/exports",
            auth=("demo_operator", TEST_PASSWORD),
            json={"request_id": str(uuid4())},
        )
        assert exported.status_code == 200, exported.text
        item = exported.json()["data"]["items"][0]
        response = client.get(item["download"], auth=("demo_operator", TEST_PASSWORD))
        assert (
            response.status_code == 200 and response.headers["x-content-sha256"] == item["sha256"]
        )
        assert client.get(item["download"], auth=("t15_other", TEST_PASSWORD)).status_code == 404
    with Session(engine) as session:
        assert counts == (
            session.scalar(select(func.count()).select_from(TestRun)),
            session.scalar(select(func.count()).select_from(AssertionResult)),
        )


def test_unfinished_run_does_not_get_report_or_candidates(lab, completed):
    engine, _, settings, runtime, runs, _ = lab
    with Session(engine) as session:
        case_id = session.scalar(select(TestExecution.case_revision_id))
    identifier = runs.create(
        1,
        CreateRun(
            request_id=uuid4(), target_version_id=runtime.version_id, case_revision_ids=[case_id]
        ),
    )
    service = ReportService(engine, settings)
    for operation in (service.read, service.collect):
        with pytest.raises(AgentError) as failure:
            operation(1, identifier)
        assert failure.value.status_code == 409
    runs.cancel(1, identifier)
    cancelled = service.read(1, identifier)
    assert cancelled["statistics"]["reliability"]["rate"] is None
    assert not service.collect(1, identifier)["items"]


def test_condition_check_detects_seed_limits_gateway_and_manual_input(lab, completed):
    original = ReportService(lab[0], lab[2]).read(1, completed[2])
    for key, value in (("seed", 99), ("limits", {}), ("repetitions", 99)):
        changed = deepcopy(original)
        changed["run"]["manifest"][key] = value
        result = compare(original, changed)
        assert not result["conditions_match"] and result["reliability_rate_delta"] is None
    changed = deepcopy(original)
    changed["executions"][0]["external_user_inputs"] = ["不同的人工确认输入"]
    assert not compare(original, changed)["conditions_match"]
    changed = deepcopy(original)
    changed["executions"][0]["gateway_mode"] = "real_provider"
    assert not compare(original, changed)["conditions_match"]


def test_grouping_and_same_condition_repair_verification_use_real_offline_SQL(lab, monkeypatch):
    """Version metadata is an explicit control fixture, never a real V1 registration."""
    engine, redis, settings, runtime, runs, _ = lab
    model = OfflineGateway()
    text = model.preview()
    case_id = add_case(engine, [{"user": text}, {"user": "确认"}], approved=False)
    with Session(engine) as session, session.begin():
        case = session.get(TestCase, case_id)
        case.source = "T15_explicit_offline_policy_control_not_real_Agent_quality"
        case.expected_json = {
            "schema_version": "1.1",
            "assertions": [{"type": "booking_count", "equals": 1}],
        }
        case.content_hash = canonical_hash(case_content(case))
        case.review_status, case.reviewer_id, case.reviewed_at = "approved", 2, REFERENCE

    def create(version):
        run = runs.create(
            1, CreateRun(request_id=uuid4(), target_version_id=version, case_revision_ids=[case_id])
        )
        return run, runs.read(1, run)["executions"][0]["execution_id"]

    failed_runs = []
    for _ in range(2):
        run, execution = create(runtime.version_id)
        bad = OfflineGateway()
        bad.queue.extend([AIMessage(content="明确离线故障控制：未执行预约。") for _ in range(2)])
        assert (
            run_execution(
                engine, redis, settings, execution, gateway_factory=lambda *_, gateway=bad: gateway
            )["status"]
            == "failed"
        )
        failed_runs.append(run)
    reports = ReportService(engine, settings)
    first = reports.collect(1, failed_runs[0])["items"][0]
    second = reports.collect(1, failed_runs[1])["items"][0]
    assert first["defect_key"] == second["defect_key"]
    detail = reports.defect(1, first["defect_key"])
    assert len(detail["occurrences"]) == 2
    original_assertions = reports.read(1, failed_runs[0])["executions"][0]["assertions"]

    def control_manifest(*args, **kwargs):
        value = build_manifest(*args, **kwargs)
        value["test_control_variant"] = "T15 mechanical repair-version fixture; not real V1"
        value["config_hash"] = canonical_hash(
            {key: item for key, item in value.items() if key != "config_hash"}
        )
        return value

    with monkeypatch.context() as patch:
        patch.setattr("app.agents.runtime.build_manifest", control_manifest)
        patch.setattr("app.executors.service.build_manifest", control_manifest)
        repaired_runtime = AgentRuntime(engine, redis, settings, auto_register=True)
        repaired_run, repaired_execution = create(repaired_runtime.version_id)

        class RepairGateway(OfflineGateway):
            def invoke(self, *args, **kwargs):
                if not self.queue:
                    with Session(engine) as session:
                        output = session.scalar(
                            select(TestExecution).where(
                                TestExecution.execution_uuid == repaired_execution
                            )
                        ).final_output_json
                    self.confirm(output)
                return super().invoke(*args, **kwargs)

        good = RepairGateway()
        good.preview()
        result = run_execution(
            engine, redis, settings, repaired_execution, gateway_factory=lambda *_: good
        )
        assert result["status"] == "passed", runs.read(1, repaired_run)

    comparison = reports.comparison(1, failed_runs[0], repaired_run)
    assert comparison["conditions_match"] and not comparison["formal_v0_v1_experiment"]
    assert len(reports.read(1, repaired_run)["executions"][0]["after"]) == 1
    for decision in ("confirmed", "fixed", "verified"):
        detail = reports.defect(1, first["defect_key"])
        values = {
            "request_id": uuid4(),
            "expected_hash": detail["revision_hash"],
            "decision": decision,
            "note": "机械控制复核：离线策略/版本夹具，真实SQL；不代表团队真人审核或V1质量。",
            "human_attestation": True,
        }
        if decision in {"fixed", "verified"}:
            values["fixed_version_id"] = repaired_runtime.version_id
        if decision == "verified":
            values["verification_execution_id"] = repaired_execution
        result = reports.review(1, first["defect_key"], DefectReview(**values))
        assert result["after"]["status"] == decision
    assert reports.read(1, failed_runs[0])["executions"][0]["assertions"] == original_assertions
