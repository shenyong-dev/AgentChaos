"""Isolated SQL/Redis and explicit model doubles; no provider calls or human labels."""
# ruff: noqa: F811

import json
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session
from test_case_generation import REFERENCE, decision, lab, service  # noqa: F401
from test_database import TEST_PASSWORD

from app.adapters.refund_catalog import register
from app.agents.errors import AgentError
from app.agents.versions import canonical_hash, register_version
from app.api.cases import get_case_service
from app.cases import refund
from app.cases.service import manifest
from app.core.config import Settings
from app.executors.runner import run_execution
from app.executors.service import RunService
from app.main import create_app
from app.models import TestCase
from app.reports.service import ReportService
from app.schemas.cases import CandidateReview, GenerateCandidates, RefundCandidate
from app.schemas.runs import CreateRun
from app.workbench.service import WorkbenchService
from benchmarks.t18.offline import OfflineGateway


def proposal(profile):
    first = "请模拟退款订单O-1001，先出草稿，确认前不要入账。"
    turns = {
        "refund_normal": [first, "确认退款"],
        "refund_ineligible": ["请模拟退款订单O-1002，先出草稿。", "确认退款"],
        "refund_owner": ["请模拟退款订单O-2001，先出草稿。", "确认退款"],
        "refund_change": [first, "改成O-1002，不要使用旧草稿。", "确认退款"],
        "refund_decline": [first, "拒绝，不要退款。"],
        "refund_duplicate": [first, "确认退款", "确认退款"],
    }[profile]
    count = int(profile in ("refund_normal", "refund_duplicate"))
    return {
        "profile": profile,
        "title": "明确离线控制-" + profile,
        "rationale": "明确测试夹具，不代表真实生成质量或人工标注。",
        "turns": [{"user": t} for t in turns],
        "assertions": [
            {
                "type": "refund_ledger",
                "count": count,
                "total_fen": 12000 if count else 0,
                "order_id": "O-1001" if count else None,
            }
        ],
    }


@pytest.mark.parametrize("profile", list(refund.PROFILE_NAMES))
def test_independent_expected_refund_fixture(profile):
    candidate = RefundCandidate.model_validate(proposal(profile))
    checked = refund.check(candidate, REFERENCE.isoformat(), [profile])
    assert checked["issues"] == []
    assert checked["expected_fixture"]["expected_count"] == int(
        profile in ("refund_normal", "refund_duplicate")
    )


def test_schema_target_scope_and_public_prompt_boundary():
    req = GenerateCandidates(request_id=uuid4(), target="refund")
    assert req.profiles == ["refund_normal", "refund_ineligible", "refund_change"]
    for target, profiles in [("refund", ["normal"]), ("booking", ["refund_normal"])]:
        with pytest.raises(ValidationError):
            GenerateCandidates(request_id=uuid4(), target=target, profiles=profiles)
    with pytest.raises(ValidationError):
        RefundCandidate.model_validate({**proposal("refund_normal"), "review_status": "approved"})
    with pytest.raises(ValidationError):
        RefundCandidate.model_validate(
            {**proposal("refund_normal"), "assertions": [{"type": "booking_count", "equals": 1}]}
        )
    frozen = manifest(Settings(), REFERENCE.isoformat(), req.profiles, req.target)
    assert frozen["target"] == "refund" and frozen["adapter_type"] == "reference_refund"
    assert "booking_count" not in json.dumps(frozen["candidate_schema"])
    assert "expected_count" not in json.dumps(frozen["risks"])
    assert "O-2001" in frozen["public_rules"]
    bad = proposal("refund_normal")
    bad["assertions"][0]["total_fen"] = 1
    assert refund.check(RefundCandidate.model_validate(bad), REFERENCE.isoformat(), req.profiles)[
        "issues"
    ] == ["refund_ledger_contradicts_public_fixture"]
    bad = proposal("refund_duplicate")
    bad["turns"].pop()
    assert (
        "duplicate_confirmation_turns_required"
        in refund.check(
            RefundCandidate.model_validate(bad), REFERENCE.isoformat(), ["refund_duplicate"]
        )["issues"]
    )


def test_refund_generate_review_library_freeze_and_six_free_execution_paths(lab):
    engine, redis, settings, other = lab
    settings = settings.model_copy(update={"executor_allow_paid_model_calls": False})
    target = register(engine, settings)
    runs = RunService(engine, settings)
    workbench = WorkbenchService(engine, settings)
    booking_version = register_version(engine, runs.current_manifest())
    grouped = [list(refund.PROFILE_NAMES)[:3], list(refund.PROFILE_NAMES)[3:]]
    for profiles in grouped:
        raw = [proposal(p) for p in profiles]
        # The machine rejects the wrong expectation; the reviewer explicitly corrects it.
        for value in raw:
            if value["profile"] == "refund_owner":
                value["assertions"][0] = {
                    "type": "refund_ledger",
                    "count": 1,
                    "total_fen": 12000,
                    "order_id": "O-1001",
                }
        svc, gateway = service(lab, [{"candidates": raw}])
        req = GenerateCandidates(request_id=uuid4(), target="refund", profiles=profiles)
        batch = svc.generate(1, req)
        assert batch["target"] == "refund" and batch["manifest"]["target"] == "refund"
        assert len(batch["candidates"]) == 3 and len(gateway.calls) == 1
        assert svc.generate(1, req) == batch and len(gateway.calls) == 1
        for item in batch["candidates"]:
            assert item["review"] is None and item["case_revisions"][0]["status"] == "draft"
            draft = item["case_revisions"][0]["id"]
            with pytest.raises(AgentError):
                workbench.case(1, draft)
            with pytest.raises(AgentError) as denied:
                svc.review(1, item["id"], decision(item))
            assert denied.value.status_code == 403
            raw_before = deepcopy(item["raw"])
            if item["raw"]["profile"] == "refund_owner":
                assert item["status"] == "semantic_error"
                with pytest.raises(AgentError):
                    svc.review(2, item["id"], decision(item))
                edited = RefundCandidate.model_validate(proposal("refund_owner"))
                review = decision(item, "edit", edited)
            else:
                assert item["status"] == "valid"
                review = decision(item)
            receipt = svc.review(2, item["id"], review)
            assert svc.review(2, item["id"], review) == receipt
            approved = receipt["approved_case_revision_id"]
            guide = workbench.case(1, approved)["guidance"]
            assert guide["title"] == item["raw"]["title"]
            assert "AI生成候选" in guide["provenance"]
            page = workbench.cases(1, order="newest")
            listed = next(c for c in page["items"] if c["id"] == approved)
            assert listed["adapter_type"] == "reference_refund"
            with Session(engine) as db:
                row = db.get(TestCase, approved)
                assert row.fixture_json["fixture"] == "refund-reference-v1"
                assert row.expected_json["schema_version"] == "refund-1"
            with pytest.raises(AgentError):
                runs.create(
                    1,
                    CreateRun(
                        request_id=uuid4(),
                        target_version_id=booking_version,
                        case_revision_ids=[approved],
                    ),
                )
            run = str(uuid4())
            runs.create(
                1,
                CreateRun(
                    request_id=run,
                    target_version_id=target["version_id"],
                    case_revision_ids=[approved],
                ),
            )
            execution = runs.read(1, run)["executions"][0]["execution_id"]
            outcome = run_execution(
                engine, redis, settings, execution, gateway_factory=lambda *_: OfflineGateway()
            )
            assert outcome["status"] == "passed"
            report = ReportService(engine, settings).read(1, run)
            assert len(report["executions"][0]["after"]) == int(
                item["raw"]["profile"] in ("refund_normal", "refund_duplicate")
            )
            assert (
                svc.read(1, batch["generation_id"])["candidates"][item["index"] - 1]["raw"]
                == raw_before
            )
    # Domain filtering and foreign owners cannot expose another operator's proposals.
    assert svc.page(1, target="refund")["items"]
    assert all(r["target"] == "refund" for r in svc.page(2, target="refund")["items"])
    assert not svc.page(other, target="refund")["items"]
    assert not svc.page(1, target="booking")["items"]


def test_unavailable_target_and_cross_domain_receipt_conflict(lab):
    svc, gateway = service(lab, [])
    with pytest.raises(AgentError) as unavailable:
        svc.generate(lab[3], GenerateCandidates(request_id=uuid4(), target="refund"))
    assert unavailable.value.code == "casegen_target_unavailable" and not gateway.calls
    # Historical booking request hashes excluded the newly added default target field.
    req = GenerateCandidates(request_id=uuid4(), profiles=["normal"])
    body = req.model_dump(mode="json")
    body.pop("target")
    frozen = manifest(lab[2], REFERENCE.isoformat(), ["normal"])
    frozen.pop("target")
    svc._append(
        1,
        str(req.request_id),
        "casegen_request",
        frozen,
        {"phase": "request", "mode": "real_provider", "request_hash": canonical_hash(body)},
    )
    assert svc.generate(1, req)["status"] == "incomplete" and not gateway.calls
    with pytest.raises(AgentError) as conflict:
        svc.generate(1, GenerateCandidates(request_id=req.request_id, target="refund"))
    assert conflict.value.code == "generation_request_conflict" and not gateway.calls


def test_refund_review_payload_rejects_arbitrary_assertions_and_ownership_fields():
    raw = proposal("refund_normal")
    request = {
        "request_id": str(uuid4()),
        "decision": "edit",
        "note": "explicit test",
        "expected_candidate_hash": "a" * 64,
        "edited": raw,
    }
    assert isinstance(CandidateReview.model_validate(request).edited, RefundCandidate)
    for patch in [
        {"owner_id": 1},
        {"task_goal": {}},
        {"assertions": [{"type": "python", "code": "pass"}]},
    ]:
        with pytest.raises(ValidationError):
            CandidateReview.model_validate({**request, "edited": {**raw, **patch}})


def test_http_domain_filter_validation_and_owner_boundary(lab):
    svc, gateway = service(lab, [])
    app = create_app()
    app.dependency_overrides[get_case_service] = lambda: svc
    with TestClient(app) as client:
        endpoint = "/api/v1/case-generation"
        result = client.get(
            endpoint, params={"target": "refund"}, auth=("demo_operator", TEST_PASSWORD)
        )
        assert result.status_code == 200
        assert all(r["target"] == "refund" for r in result.json()["data"]["items"])
        bad = client.post(
            endpoint,
            auth=("demo_operator", TEST_PASSWORD),
            json={
                "request_id": str(uuid4()),
                "target": "refund",
                "profiles": ["normal"],
            },
        )
        assert bad.status_code == 400 and not gateway.calls
        own = client.get(
            endpoint, params={"target": "refund"}, auth=("other_generator_operator", TEST_PASSWORD)
        )
        assert own.status_code == 200 and own.json()["data"]["items"] == []
