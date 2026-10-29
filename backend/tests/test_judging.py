"""T12 explicit offline infrastructure controls; none of these labels are real human calibration."""

import json
import os
from uuid import uuid4

import pytest
from agent_test_support import OfflineGateway
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import ValidationError
from redis import Redis
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate

from app.agents.errors import AgentError
from app.api.judging import get_judge_service
from app.core.config import Settings
from app.core.passwords import hash_password
from app.db.seeds import seed_database
from app.judging.budget import AUTHORIZATION, JudgeBudget
from app.judging.engine import agreement, interpret
from app.judging.service import JudgeService, load_dataset
from app.main import create_app
from app.models import User
from app.schemas.judging import DisagreementReview, HumanLabel, Judgment, RequestID, SealLabels


def answer(label="supported", explanation="reasonable", confidence=0.9):
    uncertain = confidence < 0.8 or label == "insufficient" or explanation == "insufficient"
    return {
        "support": {
            "label": label,
            "confidence": confidence,
            "reason": "Explicit offline evaluation control",
            "evidence_refs": ["public_rules"],
        },
        "explanation": {
            "label": explanation,
            "confidence": confidence,
            "reason": "Explicit offline control",
            "evidence_refs": ["dialogue"],
        },
        "conclusion": "uncertain"
        if uncertain
        else "problematic"
        if label == "contradicted" or explanation == "unreasonable"
        else "acceptable",
        "needs_review": uncertain,
        "suspected_cause": None,
    }


def test_judge_strict_missing_fields_unknown_references_contradiction_and_uncertainty():
    payload = load_dataset()["samples"][0]["payload"]
    assert interpret(answer(), payload)["status"] == "passed"
    assert interpret(answer("contradicted"), payload)["status"] == "failed"
    assert interpret(answer(confidence=0.79), payload)["status"] == "inconclusive"
    assert interpret({"support": {}}, payload)["status"] == "error"
    value = answer()
    value["support"]["evidence_refs"] = ["invented-sql-id"]
    assert interpret(value, payload)["reason"] == "judge_unknown_reference"
    value = answer()
    value["conclusion"] = "problematic"
    assert interpret(value, payload)["reason"] == "judge_contradictory_output"
    value = answer()
    value["needs_review"] = "false"
    with pytest.raises(ValidationError):
        Judgment.model_validate(value)


def test_agreement_keeps_failure_denominators_and_does_not_invent_inter_human_reliability():
    human = {
        "a": {"support": "supported", "explanation": "reasonable"},
        "b": {"support": "contradicted", "explanation": "unreasonable"},
        "c": {"support": "supported", "explanation": "reasonable"},
    }
    ai = {
        "a": interpret(answer(), {"evidence": [{"id": "public_rules"}]}),
        "b": interpret(answer(), {"evidence": [{"id": "public_rules"}]}),
        "c": {"status": "error"},
    }
    report = agreement(human, ai)
    assert (
        report["dimensions"]["support"]["n"] == 2
        and report["dimensions"]["support"]["agreement"] == 0.5
    )
    assert report["failure_rate"] == pytest.approx(1 / 3)
    assert report["needs_review"] == ["b", "c"]
    assert agreement({}, {})["dimensions"]["support"]["cohen_kappa"] is None


@pytest.fixture(scope="module")
def lab():
    if os.getenv("AC_INTEGRATION") != "1":
        pytest.skip("Real SQL/Redis, no model calls")
    settings = Settings()
    r = Redis.from_url(settings.redis_url, socket_timeout=15)
    with disposable_database("T12") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        with Session(engine) as s, s.begin():
            s.add(
                User(
                    username="t12_other",
                    role="operator",
                    enabled=True,
                    password_hash=hash_password(TEST_PASSWORD),
                )
            )
        yield engine, r, settings
    r.close()


def service(lab, outputs):
    gateway = OfflineGateway()
    gateway.queue.extend(
        [AIMessage(content=json.dumps(v)) if isinstance(v, dict) else v for v in outputs]
    )
    return JudgeService(*lab, gateway_factory=lambda *_: gateway), gateway


def label_all(svc, batch):
    values = {}
    for sample in batch["frozen"]["dataset"]["samples"]:
        value = HumanLabel(
            request_id=uuid4(),
            sample_hash=sample["sample_hash"],
            support="supported",
            explanation="reasonable",
            note="AUTOMATED INFRASTRUCTURE CONTROL LABEL PRIVATE_SENTINEL, not real human approval",
        )
        values[sample["id"]] = svc.label(2, batch["batch_id"], sample["id"], value)
        assert svc.label(2, batch["batch_id"], sample["id"], value) == values[sample["id"]]
    return values


def test_label_seal_is_required_before_judge_and_no_human_or_rule_labels_cross_boundary(lab):
    svc, gateway = service(lab, [answer()])
    request = RequestID(request_id=uuid4())
    batch = svc.create(1, request)
    assert svc.create(1, request) == batch and not gateway.calls
    sample = batch["frozen"]["dataset"]["samples"][0]
    sid = sample["id"]
    bid = batch["batch_id"]
    with pytest.raises(AgentError) as exc:
        svc.evaluate(1, bid, sid, RequestID(request_id=uuid4()))
    assert exc.value.code == "judge_labels_not_sealed" and not gateway.calls
    with pytest.raises(AgentError):
        svc.seal(
            2,
            bid,
            SealLabels(
                request_id=uuid4(),
                dataset_hash=batch["frozen"]["dataset"]["dataset_hash"],
                independent_from_judge=True,
            ),
        )
    labels = label_all(svc, batch)
    seal = svc.seal(
        2,
        bid,
        SealLabels(
            request_id=uuid4(),
            dataset_hash=batch["frozen"]["dataset"]["dataset_hash"],
            independent_from_judge=True,
        ),
    )
    assert len(seal["labels"]) == 30
    with pytest.raises(AgentError):
        svc.label(
            2,
            bid,
            sid,
            HumanLabel(
                request_id=uuid4(),
                sample_hash=sample["sample_hash"],
                support="contradicted",
                explanation="unreasonable",
                note="must not overwrite frozen labels",
            ),
        )
    call = RequestID(request_id=uuid4())
    result = svc.evaluate(1, bid, sid, call)
    assert svc.evaluate(1, bid, sid, call) == result and len(gateway.calls) == 1
    messages = gateway.calls[0]
    serialized = json.dumps([m.content for m in messages])
    assert (
        "PRIVATE_SENTINEL" not in serialized
        and "rule_observation" not in serialized
        and "source_execution_id" not in serialized
    )
    assert json.loads(messages[1].content) == sample["payload"]
    read = svc.read(1, bid)
    assert read["labels"] == labels and read["evaluation_protocol"]
    review = DisagreementReview(
        request_id=uuid4(),
        support="contradicted",
        explanation="unreasonable",
        note="AUTOMATED discrepancy control, not a human semantic conclusion",
        original_label_hash=read["label_hashes"][sid],
        ai_result_hash=result["result_hash"],
    )
    receipt = svc.review(2, bid, sid, review)
    assert svc.review(2, bid, sid, review) == receipt
    assert svc.read(1, bid)["labels"] == labels
    app = create_app()
    app.dependency_overrides[get_judge_service] = lambda: svc
    with TestClient(app) as c:
        path = "/api/v1/judging/calibrations/" + bid
        assert c.get(path).status_code == 401
        assert c.get(path, auth=("demo_operator", TEST_PASSWORD)).status_code == 200
        assert c.get(path, auth=("demo_reviewer", TEST_PASSWORD)).status_code == 200
        assert c.get(path, auth=("t12_other", TEST_PASSWORD)).status_code == 404
        body = HumanLabel(
            request_id=uuid4(),
            sample_hash=sample["sample_hash"],
            support="supported",
            explanation="reasonable",
            note="unauthorized",
        ).model_dump(mode="json")
        assert (
            c.post(
                path + f"/samples/{sid}/label", json=body, auth=("demo_operator", TEST_PASSWORD)
            ).status_code
            == 403
        )
    with pytest.raises(RuntimeError):
        migrate(lab[0], "downgrade", "20261028_0004")


@pytest.mark.parametrize(
    "value",
    [
        AIMessage(content="not JSON"),
        AIMessage(content="{}"),
        AgentError("model_timeout", "explicit offline timeout", 504),
    ],
)
def test_judge_failures_do_not_become_pass_or_retry(lab, value):
    svc, gateway = service(lab, [value])
    batch = svc.create(1, RequestID(request_id=uuid4()))
    label_all(svc, batch)
    svc.seal(
        2,
        batch["batch_id"],
        SealLabels(
            request_id=uuid4(),
            dataset_hash=batch["frozen"]["dataset"]["dataset_hash"],
            independent_from_judge=True,
        ),
    )
    sid = batch["frozen"]["dataset"]["samples"][0]["id"]
    result = svc.evaluate(1, batch["batch_id"], sid, RequestID(request_id=uuid4()))
    assert result["status"] == "error" and result["needs_review"]
    assert (
        svc.evaluate(1, batch["batch_id"], sid, RequestID(request_id=uuid4())) == result
        and len(gateway.calls) == 1
    )


def test_t12_budget_closed_missing_shared_limit_and_unknown_holds(lab):
    _, r, s = lab
    prefix = "ac:t12:test:" + uuid4().hex
    events = []
    settings = s.model_copy(
        update={
            "judge_allow_paid_model_calls": True,
            "judge_budget_unlimited": True,
            "judge_budget_authorization_id": AUTHORIZATION,
        }
    )
    budget = JudgeBudget(
        r, settings, str(uuid4()), lambda k, v: events.append((k, v)), prefix=prefix
    )
    try:
        with pytest.raises(AgentError):
            budget.reserve(1000, 1024)
        assert not r.exists(budget.key)
        r.hset(budget.key, "limit", -1)
        ticket = budget.reserve(1000, 1024)
        budget.unknown(ticket)
        assert budget.snapshot()["held_upper"] > 0 and budget.snapshot()["calls_unknown"] == 1
        budget.settle(ticket, {"input_tokens": 100, "output_tokens": 20})
        assert budget.snapshot()["held_upper"] == 0
        r.hset(budget.calls_key, "calls_started", 30)
        with pytest.raises(AgentError):
            budget.reserve(1000, 1024)
    finally:
        keys = list(r.scan_iter(match=prefix + "*"))
        if keys:
            r.delete(*keys)


def test_sealed_execution_retains_rule_ai_and_human_sources_without_business_side_effects(lab):
    from test_executor import add_case

    from app.agents.runtime import AgentRuntime
    from app.evaluation.service import EvaluationService
    from app.executors.runner import run_execution
    from app.executors.service import RunService
    from app.judging.executions import ExecutionJudgeService
    from app.schemas.judging import ExecutionJudgeRequest, ExecutionJudgeReview
    from app.schemas.runs import CreateRun

    svc, _ = service(lab, [answer() for _ in range(30)])
    batch = svc.create(1, RequestID(request_id=uuid4()))
    bid = batch["batch_id"]
    label_all(svc, batch)
    svc.seal(
        2,
        bid,
        SealLabels(
            request_id=uuid4(),
            dataset_hash=batch["frozen"]["dataset"]["dataset_hash"],
            independent_from_judge=True,
        ),
    )
    for sample in batch["frozen"]["dataset"]["samples"]:
        svc.evaluate(1, bid, sample["id"], RequestID(request_id=uuid4()))
    engine, redis, settings = lab
    settings = settings.model_copy(
        update={"executor_namespace": "ac:t12:execution-test:" + uuid4().hex}
    )
    runtime = AgentRuntime(engine, redis, settings, auto_register=True)
    runs = RunService(engine, settings)
    identifier = runs.create(
        1,
        CreateRun(
            request_id=uuid4(),
            target_version_id=runtime.version_id,
            case_revision_ids=[add_case(engine, turns=[{"user": "你好，不要预约。"}])],
        ),
    )
    execution = runs.read(1, identifier)["executions"][0]["execution_id"]
    gateway = OfflineGateway()
    gateway.queue.append(AIMessage(content="明确离线控制，不预约；没有写入。"))
    run_execution(engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    evaluator = EvaluationService(engine, settings)
    before = evaluator.read(1, execution)
    provider = OfflineGateway()
    provider.queue.append(AIMessage(content=json.dumps(answer())))
    judge = ExecutionJudgeService(engine, redis, settings, gateway_factory=lambda _: provider)
    request = ExecutionJudgeRequest(request_id=uuid4(), calibration_batch_id=bid)
    result = judge.evaluate(1, execution, request)
    assert result["status"] == "passed" and len(provider.calls) == 1
    assert judge.evaluate(1, execution, request) == result and len(provider.calls) == 1
    assert evaluator.read(1, execution) == before
    assert judge.read(1, execution)["items"] == [result]
    assert "PRIVATE_SENTINEL" not in json.dumps([m.content for m in provider.calls[0]])
    decision = ExecutionJudgeReview(
        request_id=uuid4(),
        support="insufficient",
        explanation="insufficient",
        note="AUTOMATED infrastructure review control, not a real human label",
        ai_result_hash=result["result_hash"],
    )
    review = judge.review(2, execution, decision)
    assert judge.review(2, execution, decision) == review
    assert evaluator.read(1, execution) == before
    assert judge.read(1, execution)["items"] == [result]
    assert not runs.read(1, identifier)["executions"][0]["after"]
    with pytest.raises(AgentError):
        judge.read(3, execution)
    runtime.saver.delete_thread(runs.read(1, identifier)["executions"][0]["session_id"])
    keys = list(redis.scan_iter(match=settings.executor_namespace + "*"))
    if keys:
        redis.delete(*keys)
