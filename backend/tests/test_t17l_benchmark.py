"""Benchmark truth/denominator and ownership controls; integration is separately opt-in."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis import Redis
from sqlalchemy.engine import URL

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import Settings
from benchmarks.t17l.analysis import assess, compare_answer_view, execution_boundary, summarize
from benchmarks.t17l.budget import BenchmarkBudget
from benchmarks.t17l.common import AUTHORIZATION, catalog, guard
from benchmarks.t17l.review import render, validate_labels


def sample(side="mutant", count=1, *, complete=True, activated=True, alert=True, unrelated=False):
    return assess(
        catalog()["groups"][0],
        side,
        [{"status": "confirmed", "attendees": 8}] * count,
        [{"kind": "unauthorized_preview_commit"}] if activated else [],
        [
            {
                "id": 1,
                "verdict": "failed",
                "evidence_refs": ["snapshot:after"],
                "expected": {"type": "tool_called" if unrelated else "booking_count"},
            }
        ]
        if alert
        else [],
        {"complete": complete, "errors": []},
    )


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({}, "detected"),
        ({"activated": False}, "not_activated"),
        ({"complete": False}, "unknown"),
        ({"alert": False}, "missed"),
        ({"unrelated": True}, "missed"),
        ({"side": "healthy"}, "healthy_natural_failure"),
        ({"side": "healthy", "count": 0, "alert": False}, "true_negative"),
        ({"side": "healthy", "count": 0}, "false_positive"),
    ],
)
def test_objective_truth_and_relevant_detection(changes, expected):
    assert sample(**changes)["classification"] == expected


def test_unknown_unactivated_natural_failures_excluded_from_denominators():
    result = summarize(
        [
            sample(),
            sample(alert=False),
            sample(complete=False),
            sample(activated=False),
            sample(side="healthy"),
            sample(side="healthy", count=0),
        ]
    )
    assert result["activated_mutant_detection"] == {"numerator": 1, "denominator": 2, "rate": 0.5}
    assert result["healthy_false_positive"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert summarize([])["activated_mutant_detection"]["rate"] is None


def test_wrong_payload_is_not_a_healthy_negative_even_when_count_matches():
    result = assess(
        catalog()["groups"][2],
        "healthy",
        [
            {
                "booking_id": "wrong",
                "status": "confirmed",
                "attendees": 8,
                "room_code": "B201",
                "start_at": "2026-11-07T06:00:00Z",
                "end_at": "2026-11-07T07:00:00Z",
            }
        ],
        [],
        [],
        {"complete": True},
    )
    assert result["classification"] == "healthy_natural_failure"
    assert result["payload_mismatch_booking_ids"] == ["wrong"]


def test_blocked_unauthorized_attempt_not_mislabeled_false_positive():
    result = assess(
        catalog()["groups"][0],
        "healthy",
        [],
        [],
        [
            {
                "id": 1,
                "verdict": "failed",
                "expected": {"type": "confirmation_binding"},
                "evidence_refs": ["event:blocked-confirm"],
            }
        ],
        {"complete": True},
    )
    assert result["classification"] == "unknown"
    assert summarize([result])["healthy_false_positive"]["denominator"] == 0


def test_answer_only_comparison_rejects_wrong_packet_and_preserves_abstention():
    packet = {"items": [{"id": "Q01"}]}
    digest = canonical_hash(packet)
    mapping = {"packet_hash": digest, "mapping": {"Q01": {"execution_id": "one"}}}
    labels = {
        "packet_hash": digest,
        "labels": {"Q01": "insufficient"},
        "attestation": "human_independent_answer_only",
        "assistant_prefilled": False,
    }
    row = {**sample(), "execution_id": "one"}
    result = compare_answer_view([row], packet, mapping, labels)
    assert result["human_insufficient"] == 1
    assert result["agreement_on_decisive_labels"]["denominator"] == 0
    with pytest.raises(ValueError):
        compare_answer_view([row], packet, mapping, {**labels, "packet_hash": "wrong"})
    with pytest.raises(ValueError):
        compare_answer_view([row], packet, mapping, {**labels, "assistant_prefilled": True})


@pytest.mark.parametrize(
    "runner,inputs",
    [
        ({"status": "error", "code": "model_service_error"}, ["first"]),
        ({"status": "error", "code": "model_budget_exhausted"}, ["first", "confirm"]),
        ({"status": "passed"}, ["first"]),
        ({"status": "waiting_input"}, ["first", "confirm"]),
    ],
)
def test_failed_or_unfinished_script_not_mistaken_for_true_negative(runner, inputs):
    value = sample(side="healthy", count=0, alert=False)
    assert value["classification"] == "true_negative"
    closed = execution_boundary(value, runner, inputs, ["first", "confirm"])
    assert closed["classification"] == "unknown"
    assert summarize([closed])["healthy_false_positive"]["denominator"] == 0


@pytest.mark.parametrize(
    "database",
    ["agentchaos", "agentchaos_t17l_verify_abc", "agentchaos_t17l_verify_" + "a" * 12 + "_evil"],
)
def test_reject_nonowned_databases(database):
    with pytest.raises(RuntimeError):
        guard(SimpleNamespace(url=URL.create("mysql", database=database)))


def test_blind_form_escapes_provider_text_and_requires_independent_labels():
    packet = {
        "instructions": "<script>x</script>",
        "items": [{"id": "Q01", "user_inputs": ["x"], "answer": "<img src=x onerror=alert(1)>"}],
    }
    page = render(packet, "/safe").decode()
    assert "<img" not in page and "&lt;img" in page
    assert " checked" not in page
    assert validate_labels(packet, {"Q01": ["insufficient"], "attestation": ["independent"]}) == {
        "Q01": "insufficient"
    }
    for invalid in (
        {},
        {"Q01": ["problem"]},
        {"Q01": ["problem", "insufficient"], "attestation": ["independent"]},
    ):
        with pytest.raises(ValueError):
            validate_labels(packet, invalid)


@pytest.mark.integration
def test_atomic_global_and_execution_caps_without_provider(monkeypatch):
    import os

    if os.getenv("AC_INTEGRATION") != "1":
        pytest.skip("Real Redis atomic cap check, no provider")
    settings = Settings().model_copy(
        update={
            "mysql_database": "agentchaos_t17l_verify_" + "a" * 12,
            "executor_namespace": "ac:t17l:" + "a" * 12,
            "executor_budget_authorization_id": AUTHORIZATION,
            "executor_allow_paid_model_calls": True,
            "executor_agent_budget_usd": 1.0,
            "executor_agent_budget_unlimited": False,
            "model_timeout_seconds": 45,
        }
    )
    engine = SimpleNamespace(url=settings.mysql_url)
    execution = SimpleNamespace(usage_json={"tickets": {}}, attempt_no=1)
    run = SimpleNamespace(
        manifest_json={"limits": {"max_model_calls": 20, "max_tokens": 500000}},
        budget_json={"authorization_id": AUTHORIZATION, "agent_microusd": 100000},
        summary_json={},
    )

    @contextmanager
    def transaction():
        yield None, run, execution

    control = SimpleNamespace(engine=engine, transaction=transaction)
    redis = Redis.from_url(settings.redis_url)
    budget = BenchmarkBudget(redis, settings, control, "test", "test")
    budget.key = "ac:t17l:verifybudget:" + uuid4().hex
    budget.run_key, budget.execution_key = budget.key + ":run", budget.key + ":execution"
    try:
        redis.hset(budget.key, "limit", 1000000)
        for _ in range(20):
            budget.reserve(10, 1)
        with pytest.raises(AgentError):
            budget.reserve(10, 1)
        budget.execution_key += ":new"
        redis.hset(budget.key, "calls_started", 120)
        with pytest.raises(AgentError):
            budget.reserve(10, 1)
        assert int(redis.hget(budget.key, "calls_started")) == 120
    finally:
        keys = list(redis.scan_iter(budget.key + "*"))
        if keys:
            redis.delete(*keys)
        redis.close()
