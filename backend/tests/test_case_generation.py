"""T11 real MySQL/Redis controls; provider calls and human semantic labels are not faked."""

import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from agent_test_support import OfflineGateway
from casegen_test_support import proposal
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import ValidationError
from redis import Redis
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate

from app.agents.errors import AgentError
from app.api.booking import get_booking_service
from app.api.cases import get_case_service
from app.cases.budget import AUTHORIZATION, GenerationBudget
from app.cases.service import CaseService, manifest
from app.cases.validation import check, text_fingerprint
from app.core.config import Settings
from app.core.passwords import hash_password
from app.db.seeds import seed_database
from app.main import create_app
from app.models import Artifact, TestCase, User
from app.schemas.cases import Candidate, CandidateReview, GenerateCandidates
from app.services.booking import BookingService

REFERENCE = datetime(2026, 10, 28, 1, tzinfo=UTC)


def test_proposals_cannot_import_approval_identity_partition_or_arbitrary_assertions():
    for key, value in [
        ("review_status", "approved"),
        ("reviewer_id", 2),
        ("dataset_split", "holdout"),
    ]:
        with pytest.raises(ValidationError):
            Candidate.model_validate({**proposal(), key: value})
    with pytest.raises(ValidationError):
        Candidate.model_validate({**proposal(), "assertions": [{"type": "python", "code": "pass"}]})
    with pytest.raises(ValidationError):
        GenerateCandidates(request_id=uuid4(), profiles=["normal", "normal"])


def test_valid_json_can_have_wrong_expected_and_is_not_semantically_approved():
    candidate = Candidate.model_validate(proposal("capacity", wrong_count=True))
    result = check(candidate, REFERENCE.isoformat(), ["capacity"])
    assert result["issues"] == ["booking_count_contradicts_public_fixture"]
    good = check(Candidate.model_validate(proposal()), REFERENCE.isoformat(), ["normal"])
    assert not good["issues"] and good["warnings"]
    a = [{"user": "Ａ１０１，１０人。"}]
    b = [{"user": "a101 10人"}]
    assert text_fingerprint(a, REFERENCE.isoformat()) == text_fingerprint(b, REFERENCE.isoformat())
    s = Settings()
    frozen = manifest(s, REFERENCE.isoformat(), ["normal"])
    serialized = json.dumps(frozen, ensure_ascii=False)
    assert "holdout" not in json.dumps(frozen["risks"]) and "expected_count" not in serialized
    bad = proposal()
    bad["turns"][-1]["user"] = "确认，按上述草稿提交。"
    assert (
        "final_confirmation_not_supported"
        in check(Candidate.model_validate(bad), REFERENCE.isoformat(), ["normal"])["issues"]
    )


@pytest.fixture(scope="module")
def lab():
    if os.getenv("AC_INTEGRATION") != "1":
        pytest.skip("Real MySQL/Redis; no provider calls")
    s = Settings().model_copy(update={"casegen_allow_paid_model_calls": False})
    redis = Redis.from_url(s.redis_url, socket_timeout=15)
    with disposable_database("T11") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        with Session(engine) as session, session.begin():
            user = User(
                username="other_generator_operator",
                role="operator",
                enabled=True,
                password_hash=hash_password(TEST_PASSWORD),
            )
            session.add(user)
            session.flush()
            other = user.id
        yield engine, redis, s, other
    redis.close()


def service(lab, queue, *, clock=REFERENCE):
    engine, redis, s, _ = lab
    gateway = OfflineGateway()
    gateway.queue.extend(
        [
            AIMessage(content=json.dumps(v, ensure_ascii=False)) if isinstance(v, dict) else v
            for v in queue
        ]
    )
    return CaseService(
        engine, redis, s, gateway_factory=lambda _: gateway, clock=lambda: clock
    ), gateway


def decision(item, kind="approve", edited=None):
    return CandidateReview(
        request_id=uuid4(),
        decision=kind,
        expected_candidate_hash=item["candidate_hash"],
        note="Automated infrastructure control, not a real human semantic approval",
        edited=edited,
    )


def test_generation_drafts_idempotency_duplicate_order_and_role_bound_review(lab):
    svc, gateway = service(lab, [{"candidates": [proposal(), proposal("capacity")]}])
    req = GenerateCandidates(request_id=uuid4(), profiles=["normal", "capacity"])
    first = svc.generate(1, req)
    assert first["status"] == "completed" and first["metrics"]["valid_before_review"] == 2
    assert all(c["case_revisions"][0]["status"] == "draft" for c in first["candidates"])
    assert svc.generate(1, req) == first and len(gateway.calls) == 1
    repeated, _ = service(lab, [{"candidates": [proposal(), proposal("capacity")]}])
    second = repeated.generate(
        1, GenerateCandidates(request_id=uuid4(), profiles=["normal", "capacity"])
    )
    assert second["metrics"]["counts"]["duplicate"] == 2
    normal = first["candidates"][0]
    approve = decision(normal)
    with pytest.raises(AgentError) as err:
        svc.review(1, normal["id"], approve)
    assert err.value.status_code == 403
    receipt = svc.review(2, normal["id"], approve)
    assert receipt["approved_case_revision_id"] and receipt["reviewer_id"] == 2
    assert svc.review(2, normal["id"], approve) == receipt
    with pytest.raises(AgentError):
        svc.review(2, normal["id"], decision(normal, "reject"))
    duplicate = second["candidates"][0]
    with pytest.raises(AgentError):
        repeated.review(2, duplicate["id"], decision(duplicate))
    rejected = repeated.review(2, duplicate["id"], decision(duplicate, "reject"))
    assert rejected["approved_case_revision_id"] is None
    with Session(lab[0]) as session:
        approved = session.get(TestCase, receipt["approved_case_revision_id"])
        assert approved.review_status == "approved" and approved.revision == 2
    app = create_app()
    app.dependency_overrides[get_case_service] = lambda: svc
    app.dependency_overrides[get_booking_service] = lambda: BookingService(lab[0])
    with TestClient(app) as client:
        path = "/api/v1/case-generation/" + first["generation_id"]
        assert client.get(path).status_code == 401
        assert client.get(path, auth=("demo_operator", TEST_PASSWORD)).status_code == 200
        assert client.get(path, auth=("other_generator_operator", TEST_PASSWORD)).status_code == 404
        assert client.get(path, auth=("demo_reviewer", TEST_PASSWORD)).status_code == 200
        body = {"request_id": str(uuid4()), "profiles": ["normal"], "approved": True}
        assert (
            client.post(
                "/api/v1/case-generation", json=body, auth=("demo_operator", TEST_PASSWORD)
            ).status_code
            == 400
        )


def test_wrong_expected_requires_explicit_edit_new_revision_and_preserves_raw(lab):
    clock = REFERENCE.replace(day=29)
    bad = proposal("capacity", day="2026-10-30", wrong_count=True)
    svc, _ = service(lab, [{"candidates": [bad]}], clock=clock)
    batch = svc.generate(1, GenerateCandidates(request_id=uuid4(), profiles=["capacity"]))
    item = batch["candidates"][0]
    assert item["status"] == "semantic_error"
    with pytest.raises(AgentError):
        svc.review(2, item["id"], decision(item))
    good = deepcopy(bad)
    good["assertions"][0]["equals"] = 0
    receipt = svc.review(2, item["id"], decision(item, "edit", Candidate.model_validate(good)))
    assert receipt["changed_fields"] == ["assertions"]
    reread = svc.read(1, batch["generation_id"])
    assert reread["candidates"][0]["raw"] == bad
    assert reread["metrics"]["edited"] == 1 and len(reread["candidates"][0]["case_revisions"]) == 2
    assert (
        reread["candidates"][0]["case_revisions"][-1]["candidate"]["assertions"][0]["equals"] == 0
    )
    with pytest.raises(RuntimeError):
        migrate(lab[0], "downgrade", "20261026_0003")


def test_current_dedup_recheck_preserves_initial_metrics_after_rejection(lab):
    clock = REFERENCE.replace(day=30)
    fixture = proposal(day="2026-10-31")
    svc, _ = service(lab, [{"candidates": [fixture]}, {"candidates": [fixture]}], clock=clock)
    first = svc.generate(1, GenerateCandidates(request_id=uuid4(), profiles=["normal"]))
    second = svc.generate(1, GenerateCandidates(request_id=uuid4(), profiles=["normal"]))
    item = second["candidates"][0]
    assert item["status"] == item["recheck"]["status"] == "duplicate"
    svc.review(2, first["candidates"][0]["id"], decision(first["candidates"][0], "reject"))
    current = svc.read(1, second["generation_id"])
    assert current["candidates"][0]["status"] == "duplicate"
    assert current["candidates"][0]["recheck"]["status"] == "valid"
    assert current["metrics"]["valid_before_review"] == 0
    receipt = svc.review(2, item["id"], decision(item))
    from app.agents.versions import register_version
    from app.executors.service import RunService
    from app.schemas.runs import CreateRun

    runs = RunService(lab[0], lab[2])
    version = register_version(lab[0], runs.current_manifest())
    approved = receipt["approved_case_revision_id"]
    with pytest.raises(AgentError) as err:
        runs.create(
            1,
            CreateRun(
                request_id=uuid4(),
                target_version_id=version,
                case_revision_ids=[item["case_revisions"][0]["id"]],
            ),
        )
    assert err.value.code == "case_not_approved"
    identifier = runs.create(
        1, CreateRun(request_id=uuid4(), target_version_id=version, case_revision_ids=[approved])
    )
    assert runs.read(1, identifier)["status"] == "queued"
    runs.cancel(1, identifier)


def test_invalid_null_candidate_and_tampered_evidence_fail_safely(lab):
    svc, _ = service(lab, [{"candidates": [None]}])
    batch = svc.generate(1, GenerateCandidates(request_id=uuid4(), profiles=["normal"]))
    assert batch["status"] == "error" and batch["result"]["error"] == "missing_requested_profiles"
    assert batch["candidates"][0]["raw"] is None
    assert batch["candidates"][0]["recheck"]["status"] == "schema_error"
    with pytest.raises(AgentError) as err:
        svc.review(
            2,
            batch["candidates"][0]["id"],
            decision(batch["candidates"][0], "edit", Candidate.model_validate(proposal())),
        )
    assert err.value.status_code == 409
    from app.core.config import PROJECT_ROOT

    with Session(lab[0]) as session:
        row = session.get(Artifact, batch["candidates"][0]["id"])
        path = PROJECT_ROOT / row.relative_path
    original = path.read_bytes()
    try:
        path.write_bytes(original + b" ")
        with pytest.raises(AgentError) as err:
            svc.read(1, batch["generation_id"])
        assert err.value.code == "casegen_evidence_invalid"
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize(
    "first_error",
    [
        AIMessage(content=""),
        AIMessage(content="{"),
        AgentError("model_output_truncated", "synthetic truncated", 503),
    ],
)
def test_format_repairs_are_finite_and_bad_schema_is_separate(lab, first_error):
    svc, gateway = service(
        lab, [first_error, {"candidates": [{"approved": True, "profile": "normal"}]}]
    )
    batch = svc.generate(1, GenerateCandidates(request_id=uuid4(), profiles=["normal"]))
    assert len(gateway.calls) == 2 and len(batch["result"]["attempts"]) == 2
    assert batch["candidates"][0]["status"] == "schema_error"
    assert not batch["candidates"][0]["case_revisions"]
    assert (
        svc.review(2, batch["candidates"][0]["id"], decision(batch["candidates"][0], "reject"))[
            "decision"
        ]
        == "reject"
    )


def test_budget_atomic_global_missing_fail_closed_and_repairs_count(lab):
    _, r, s, _ = lab
    prefix = "ac:t11:test:" + uuid4().hex
    s = s.model_copy(
        update={
            "casegen_allow_paid_model_calls": True,
            "casegen_budget_unlimited": True,
            "casegen_budget_authorization_id": AUTHORIZATION,
        }
    )
    records = []
    budget = GenerationBudget(
        r, s, str(uuid4()), lambda phase, value: records.append((phase, value)), prefix=prefix
    )
    try:
        with pytest.raises(AgentError):
            budget.reserve(1000, 2048)
        assert not r.exists(budget.key)
        r.hset(budget.key, "limit", -1)
        a = budget.reserve(1000, 2048)
        budget.settle(a, {"input_tokens": 100, "output_tokens": 20})
        b = budget.reserve(1000, 2048)
        budget.unknown(b)
        with pytest.raises(AgentError):
            budget.reserve(1000, 2048)
        assert budget.snapshot()["calls_started"] == 2 and budget.snapshot()["calls_completed"] == 1
        assert budget.snapshot()["held_upper"] > 0 and budget.snapshot()["calls_unknown"] == 1
        assert [p for p, _ in records] == ["reserved", "settled", "reserved", "unknown"]
    finally:
        keys = list(r.scan_iter(match=prefix + "*"))
        if keys:
            r.delete(*keys)
