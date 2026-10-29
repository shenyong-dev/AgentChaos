"""Real Redis atomic controls in uniquely owned keys; never initialize a paid ledger."""

import os
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis import Redis

from app.agents.errors import AgentError
from app.core.config import Settings
from experiments.budget import (
    AGENT_AUTH,
    AGENT_CAP,
    JUDGE_AUTH,
    JUDGE_CAP,
    FormalExecutorBudget,
    FormalJudgeBudget,
)


@pytest.fixture
def control_redis():
    if os.environ.get("AC_INTEGRATION") != "1":
        pytest.skip("Explicit Redis infrastructure controls")
    client = Redis.from_url(Settings().redis_url)
    prefix = "ac:t16:control:" + uuid4().hex
    try:
        yield client, prefix
    finally:
        keys = list(client.scan_iter(match=prefix + "*"))
        if keys:
            client.delete(*keys)
        client.close()


class Control:
    def __init__(self):
        self.run = SimpleNamespace(
            manifest_json={"limits": {"max_model_calls": 40, "max_tokens": 500_000}},
            budget_json={"authorization_id": AGENT_AUTH, "agent_microusd": 150_000},
            summary_json={},
        )
        self.execution = SimpleNamespace(usage_json={"tickets": {}}, attempt_no=1)

    @contextmanager
    def transaction(self):
        yield None, self.run, self.execution


def test_agent_missing_cap_fails_closed_then_atomic_cap_and_plan_call_limits(control_redis):
    redis, prefix = control_redis
    s = Settings().model_copy(
        update={
            "executor_allow_paid_model_calls": True,
            "executor_agent_budget_unlimited": False,
            "executor_budget_authorization_id": AGENT_AUTH,
        }
    )
    control = Control()
    budget = FormalExecutorBudget(redis, s, control, "run", "execution")
    budget.key, budget.run_key, budget.execution_key = prefix, prefix + ":run", prefix + ":exec"
    with pytest.raises(AgentError, match="账本缺失"):
        budget.reserve(1, 1)
    assert not redis.exists(prefix)
    redis.hset(prefix, mapping={"limit": AGENT_CAP, "calls_started": 400})
    ticket = budget.reserve(1, 1)
    assert int(redis.hget(prefix, "calls_started")) == 401
    assert control.execution.usage_json["tickets"][ticket["call_id"]]["state"] == "reserved"
    redis.hset(prefix, "held_upper", AGENT_CAP)
    before = redis.hgetall(prefix)
    with pytest.raises(AgentError):
        budget.reserve(1, 1)
    assert redis.hgetall(prefix) == before
    redis.hset(prefix, mapping={"held_upper": 0, "calls_started": 13880})
    with pytest.raises(AgentError):
        budget.reserve(1, 1)


def test_judge_unknown_holds_cap_and_settlement_is_idempotent(control_redis):
    redis, prefix = control_redis
    records = []
    s = Settings().model_copy(
        update={
            "judge_allow_paid_model_calls": True,
            "judge_budget_unlimited": False,
            "judge_budget_authorization_id": JUDGE_AUTH,
        }
    )
    budget = FormalJudgeBudget(redis, s, "batch", lambda *x: records.append(x))
    budget.key, budget.batch_key, budget.calls_key = prefix, prefix + ":batch", prefix + ":calls"
    redis.hset(prefix, "limit", JUDGE_CAP)
    ticket = budget.reserve(1, 1)
    assert budget.settle(ticket, None) is None
    assert int(redis.hget(prefix, "held_upper")) == ticket["upper_microusd"]
    assert int(redis.hget(prefix, "calls_unknown")) == 1
    usage = {"input_tokens": 1, "output_tokens": 1}
    budget.settle(ticket, usage)
    before = redis.hgetall(prefix)
    budget.settle(ticket, usage)
    assert redis.hgetall(prefix) == before
    assert int(redis.hget(prefix, "held_upper")) == 0
    redis.hset(prefix, "calls_started", 90)
    with pytest.raises(AgentError):
        budget.reserve(1, 1)


def test_wrong_role_authorization_never_opens_old_budget(control_redis):
    redis, prefix = control_redis
    budget = FormalJudgeBudget(redis, Settings(), "batch", lambda *_: None)
    budget.key = prefix
    with pytest.raises(AgentError, match="未匹配"):
        budget.reserve(1, 1)
    assert not redis.exists(prefix)
