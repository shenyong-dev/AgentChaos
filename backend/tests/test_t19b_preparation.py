"""Synthetic preparation controls, not participant answers or model quality evidence."""

import json
import os
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError
from redis import Redis
from sqlalchemy import URL

from app.agents.errors import AgentError
from app.core.config import Settings
from app.db.engine import create_database_engine
from benchmarks.t19b import budget as b
from benchmarks.t19b import copilot as port_module
from benchmarks.t19b import preparation as p
from benchmarks.t19b.copilot import TrialAssistant


@pytest.fixture
def package(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "ROOT", tmp_path)
    monkeypatch.setattr(b, "ROOT", tmp_path)
    monkeypatch.setattr(p, "sources", lambda: {"synthetic_control": "a" * 64})
    monkeypatch.setattr(p, "read_cases", lambda _: [{"synthetic_control": True}])
    return p.prepare(None)


def valid_answer(**change):
    return {
        "conclusion": "Explicit test-only unknown judgment",
        "evidence_found": False,
        "evidence_location": "",
        "evidence_excerpt": "",
        "evidence_reason": "",
        "missing_reason": "Explicit test-only missing evidence",
        "next_action": "Explicit test-only verify original source",
        "help_count": 0,
        "used_ai": False,
        "interrupted": False,
        "deviation_note": "",
        **change,
    }


def approve(package):
    folder, frozen = package
    p.exclusive_json(
        b.ROOT / "approval.json",
        {
            "approved_by": "human_user",
            "user_approval_reference": "EXPLICIT_SYNTHETIC_CONTROL_NOT_REAL_APPROVAL",
            "package_hash": frozen["package_hash"],
            "limits": b.LIMITS,
        },
    )
    return folder


def test_counterbalance_has_distinct_tasks_and_no_phase_a_task_reuse():
    value = p.protocol()
    assert {t["case_id"] for t in value["tasks"]} == {3, 4, 5, 6}
    assert all(t["case_id"] != 1 for t in value["tasks"])
    assert all(sum(c == "ai" for _, c in order) == 2 for order in value["allocation"].values())
    assert value["limits"] == b.LIMITS
    assert "不是独立人工金标准" in value["scoring"]["authority"]


def test_prepare_has_no_answers_clock_or_real_approval_and_reuses_exact_package(package):
    folder, frozen = package
    assert p.prepare(None) == package
    assert set(x.name for x in folder.iterdir()) == {"package.json", "reference-private.json"}
    assert not (b.ROOT / "approval.json").exists()
    assert p.check_current(folder, None) == frozen
    assert "answers" not in frozen and "started_at" not in frozen


@pytest.mark.parametrize("kind", ["reference", "package", "source", "case", "protocol"])
def test_package_tamper_or_current_drift_rejected_without_overwriting(package, monkeypatch, kind):
    folder, frozen = package
    original = (folder / "package.json").read_bytes()
    if kind in {"reference", "package"}:
        path = folder / ("reference-private.json" if kind == "reference" else "package.json")
        path.write_text("{}", encoding="utf-8")
    elif kind == "source":
        monkeypatch.setattr(p, "sources", lambda: {"changed_control": "b" * 64})
    elif kind == "case":
        monkeypatch.setattr(p, "read_cases", lambda _: [{"changed_control": True}])
    else:
        changed = deepcopy(frozen["protocol"])
        changed["scope"] = "explicit changed control"
        monkeypatch.setattr(p, "protocol", lambda: changed)
    with pytest.raises((ValueError, KeyError)):
        p.check_current(folder, None)
    if kind != "package":
        assert (folder / "package.json").read_bytes() == original


@pytest.mark.parametrize(
    "change",
    [
        {"conclusion": "  "},
        {"evidence_found": True},
        {"missing_reason": "  "},
        {"help_count": 1, "deviation_note": "无"},
        {"used_ai": True, "deviation_note": "none"},
        {"interrupted": True, "deviation_note": ""},
        {"help_count": True},
        {"unexpected_answer_label": "correct"},
    ],
)
def test_structured_answer_keeps_unknown_and_rejects_missing_evidence_or_empty_deviations(change):
    with pytest.raises(ValidationError):
        p.EvidenceAnswer.model_validate(valid_answer(**change))


def test_structured_answer_is_not_automatic_correctness():
    unknown = p.EvidenceAnswer.model_validate(valid_answer())
    assert not unknown.evidence_found and "correctness" not in unknown.model_dump()
    found = p.EvidenceAnswer.model_validate(
        valid_answer(
            evidence_found=True,
            evidence_location="control location",
            evidence_excerpt="control excerpt",
            evidence_reason="control reason",
            missing_reason="",
            used_ai=True,
            deviation_note="Explicit synthetic question and reply, not human evidence",
        )
    )
    assert found.evidence_found


def test_unapproved_budget_does_not_initialize_or_touch_redis(package):
    redis = Mock()
    with pytest.raises(AgentError, match="尚未确认"):
        b.initialize(redis, package[0])
    redis.eval.assert_not_called()
    trial = b.TrialBudget(redis, Settings(_env_file=None), "control", package[0], None)
    with pytest.raises(AgentError):
        trial.reserve(100, 100)
    redis.eval.assert_not_called()


def test_approval_binds_exact_package_and_limits(package):
    folder = approve(package)
    assert b.approval(folder)["package_hash"] == package[1]["package_hash"]
    path = b.ROOT / "approval.json"
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["limits"] = {**b.LIMITS, "global_requests": 73}
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(AgentError):
        b.approval(folder)


def test_guide_condition_and_unknown_participant_rejected_before_redis(package):
    folder = approve(package)
    redis = Mock()
    for person, task in [("P01", "D1"), ("P99", "D2")]:
        with pytest.raises(AgentError, match="无AI条件"):
            TrialAssistant(person, task, folder, None, redis, Settings(_env_file=None))
    redis.assert_not_called()
    assert not redis.method_calls


@pytest.fixture
def owned_redis(monkeypatch):
    if os.getenv("AC_INTEGRATION") != "1":
        pytest.skip("Requires owned Redis only; no model or SQL mutations")
    client = Redis.from_url(Settings().redis_url, decode_responses=True)
    client.ping()
    prefix = "ac:test:t19b:" + uuid4().hex
    monkeypatch.setattr(b, "LEDGER", prefix + ":budget:copilot")
    try:
        yield client, prefix
    finally:
        keys = list(client.scan_iter(prefix + ":*"))
        if keys:
            client.delete(*keys)
        client.close()


def trial_settings():
    return Settings(_env_file=None).model_copy(
        update={
            "copilot_budget_authorization_id": b.AUTHORIZATION,
            "copilot_allow_paid_model_calls": True,
            "copilot_budget_usd": 1,
            "copilot_local_unlimited_calls": False,
            "copilot_model": "deepseek-flash",
            "copilot_thinking": "disabled",
            "copilot_max_output_tokens": 1536,
            "model_timeout_seconds": 45,
        }
    )


def test_finite_reservation_settlement_and_initialize_never_reset(package, owned_redis):
    client, prefix = owned_redis
    folder = approve(package)
    b.initialize(client, folder)
    trial = b.TrialBudget(client, trial_settings(), "control", folder, None)
    ticket = trial.reserve(100, 100)
    assert client.hget(trial.key, "calls_started") == "1"
    assert int(client.hget(trial.key, "held_upper")) == ticket["upper_microusd"]
    estimate = trial.settle(ticket, {"input_tokens": 10, "output_tokens": 5})
    assert int(client.hget(trial.key, "charged_estimate")) == estimate
    assert client.hget(trial.key, "held_upper") == "0"
    before = client.hgetall(trial.key)
    b.initialize(client, folder)
    assert client.hgetall(trial.key) == before
    client.hset(trial.key, "limit", 2)
    before = client.hgetall(trial.key)
    with pytest.raises(ValueError):
        b.initialize(client, folder)
    assert client.hgetall(trial.key) == before
    assert trial.key.startswith(prefix)


def test_lost_used_ledger_cannot_reinitialize_or_reserve_under_same_approval(package, owned_redis):
    client, _ = owned_redis
    folder = approve(package)
    b.initialize(client, folder)
    trial = b.TrialBudget(client, trial_settings(), "control", folder, None)
    ticket = trial.reserve(100, 100)
    trial.settle(ticket, {"input_tokens": 10, "output_tokens": 5})
    marker = (b.ROOT / "initialization.json").read_bytes()
    # Only this test's explicitly owned temporary keys are removed.
    client.delete(trial.key, trial.session_key, trial.calls_key)
    with pytest.raises(ValueError, match="ledger lost"):
        b.initialize(client, folder)
    assert not client.exists(trial.key)
    assert (b.ROOT / "initialization.json").read_bytes() == marker
    with pytest.raises(ValueError, match="ledger lost"):
        trial.reserve(100, 100)
    assert not client.exists(trial.key)


def test_different_redis_instance_refuses_existing_allowance(package, owned_redis, monkeypatch):
    client, _ = owned_redis
    folder = approve(package)
    b.initialize(client, folder)
    before = client.hgetall(b.LEDGER)
    monkeypatch.setattr(client, "info", lambda *_: {"run_id": "explicit-other-instance-control"})
    with pytest.raises(ValueError, match="instance or approval changed"):
        b.initialize(client, folder)
    assert client.hgetall(b.LEDGER) == before


@pytest.mark.parametrize(
    "cap", ["global_calls", "session_calls", "global_money", "session_money", "missing"]
)
def test_finite_caps_fail_closed_without_changing_existing_usage(package, owned_redis, cap):
    client, _ = owned_redis
    folder = approve(package)
    trial = b.TrialBudget(client, trial_settings(), "control", folder, None)
    if cap != "missing":
        b.initialize(client, folder)
    if cap == "global_calls":
        client.hset(trial.key, "calls_started", 72)
    elif cap == "session_calls":
        client.hset(trial.calls_key, "calls_started", 12)
    elif cap == "global_money":
        client.hset(trial.key, "charged_estimate", 1000000)
    elif cap == "session_money":
        client.hset(trial.session_key, "held_upper", 100000)
    before = {k: client.hgetall(k) for k in [trial.key, trial.session_key, trial.calls_key]}
    with pytest.raises((AgentError, ValueError)):
        trial.reserve(100, 100)
    assert before == {k: client.hgetall(k) for k in before}


def test_trial_context_is_fixed_and_retries_do_not_escape_question_cap(package, owned_redis):
    client, prefix = owned_redis
    folder = approve(package)
    original = Settings(_env_file=None).model_copy(update={"copilot_local_unlimited_calls": True})
    engine = SimpleNamespace(url=URL.create("mysql+pymysql", database="synthetic_control"))
    port = TrialAssistant("P01", "D2", folder, engine, client, original)
    assert original.copilot_local_unlimited_calls
    assert not port.settings.copilot_local_unlimited_calls
    assert port.task["case_id"] == 5
    second = TrialAssistant("P01", "D2", folder, engine, client, original)
    assert second.creation_id == port.creation_id
    port.service.prefix = prefix + ":service"
    port.admissions = prefix + ":admissions"
    sid = str(uuid4())
    port.begin = Mock(return_value={"session_id": sid, "context_hash": "a" * 64})
    port.service.ask = Mock(return_value={"status": "explicit_synthetic_control"})
    first_id = uuid4()
    port.ask("explicit question one", first_id)
    with pytest.raises(AgentError, match="回执不可得"):
        port.ask("explicit question one", first_id)
    client.set(port.service.turn_key(sid, first_id), "explicit synthetic receipt")
    port.ask("explicit question one", first_id)
    with pytest.raises(AgentError, match="不能更换问题"):
        port.ask("explicit changed question", first_id)
    for _ in range(3):
        port.ask("explicit synthetic new question", uuid4())
    assert client.hget(port.admissions, "count") == "4"
    with pytest.raises(AgentError, match="四个问题"):
        port.ask("explicit fifth question", uuid4())
    assert port.service.ask.call_count == 5  # Four new plus one original receipt retrieval.


def test_actual_read_tools_and_isolated_service_loop_with_explicit_gateway_double(
    package, owned_redis, monkeypatch
):
    """Real owned SQL reads + Redis, synthetic provider; not real AI effectiveness."""
    client, prefix = owned_redis
    folder = approve(package)
    b.initialize(client, folder)
    old_keys = [
        "ac:t17e:budget:T17E-20261104-copilot-1:copilot",
        "ac:t05:budget:T10-20261028-agent-open:agent",
    ]
    before = {k: client.hgetall(k) for k in old_keys}
    seen = []

    class ExplicitGatewayDouble:
        def __init__(self, settings, budget, **kwargs):
            self.budget, self.step = budget, 0
            assert kwargs["role"] == "copilot"
            assert budget.key.startswith(prefix)

        def invoke(self, messages, tools, **kwargs):
            ticket = self.budget.reserve(100, 100)
            self.budget.settle(ticket, {"input_tokens": 10, "output_tokens": 5})
            call = {"call_id": ticket["call_id"], "role": "copilot", "status": "completed"}
            if self.step == 0:
                self.step += 1
                return AIMessage(
                    content="",
                    tool_calls=[{"name": "read_selection", "args": {}, "id": str(uuid4())}],
                ), call
            source = json.loads(messages[-1].content)
            assert source["content"]["kind"] == "case" and source["content"]["id"] == "5"
            seen.append(source["source_id"])
            return AIMessage(
                content=json.dumps(
                    {
                        "facts": [
                            {
                                "text": "Explicit mechanical answer, not human or model evidence",
                                "citations": [source["source_id"]],
                            }
                        ],
                        "possible_causes": [],
                        "missing_evidence": [],
                        "next_steps": [],
                        "action_ids": [],
                        "certainty": "limited",
                    }
                )
            ), call

    monkeypatch.setattr(port_module, "ModelGateway", ExplicitGatewayDouble)
    engine = create_database_engine(Settings())
    port = TrialAssistant("P01", "D2", folder, engine, client, Settings())
    port.service.prefix = prefix + ":service"
    port.admissions = prefix + ":admissions"
    rid = uuid4()
    result = port.ask("Explicit synthetic mechanical control", rid)
    assert result["status"] == "completed" and seen
    assert len(result["calls"]) == 2
    assert result["answer"]["facts"][0]["citations"] == seen
    assert port.receipt(rid) == result
    count = client.hget(b.LEDGER, "calls_started")
    assert port.ask("Explicit synthetic mechanical control", rid) == result
    assert client.hget(b.LEDGER, "calls_started") == count == "2"
    assert {k: client.hgetall(k) for k in old_keys} == before
    engine.dispose()
