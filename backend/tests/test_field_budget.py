"""The amended T17A cap must remain atomic and must not expand its dollar caps."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_executor as support

from app.agents.errors import AgentError
from app.executors.budget import FIELD_SOURCE_AUTHORIZATION, ExecutorBudget, authorization_limit
from app.executors.control import ExecutionControl

infrastructure = support.infrastructure
run = support.run
pytestmark = support.pytestmark


def budget_for(run, monkeypatch):
    service, make, redis, settings = run
    settings = settings.model_copy(
        update={
            "executor_budget_authorization_id": FIELD_SOURCE_AUTHORIZATION,
        }
    )
    monkeypatch.setattr(service, "settings", settings)
    identifier, execution, _ = make(budgets={"agent_microusd": 500_000})
    claim = service.claim(execution)
    control = ExecutionControl(service.engine, claim["id"], claim["token"])
    budget = ExecutorBudget(
        redis,
        settings,
        control,
        identifier,
        execution,
        prefix=settings.executor_namespace + ":t17a-test:" + uuid4().hex,
    )
    return budget, redis


def test_t17a_eighty_requests_atomic_and_original_dollar_cap(run, monkeypatch):
    budget, redis = budget_for(run, monkeypatch)
    assert authorization_limit(budget.settings) == 1_000_000
    redis.hset(budget.key, mapping={"limit": 1_000_000, "calls_started": 79})

    def reserve(_):
        try:
            return budget.reserve(100, 100)
        except AgentError as exc:
            assert exc.code == "model_budget_exhausted"
            return None

    with ThreadPoolExecutor(2) as pool:
        tickets = list(pool.map(reserve, range(2)))
    assert sum(ticket is not None for ticket in tickets) == 1
    assert budget.snapshot()["calls_started"] == 80


def test_t17a_missing_ledger_and_one_tenth_dollar_run_cap_fail_closed(run, monkeypatch):
    budget, redis = budget_for(run, monkeypatch)
    with pytest.raises(AgentError) as missing:
        budget.reserve(100, 100)
    assert missing.value.code == "budget_ledger_unavailable"
    redis.hset(budget.key, mapping={"limit": 1_000_000})
    redis.hset(budget.run_key, mapping={"charged_estimate": 99_999})
    with pytest.raises(AgentError) as capped:
        budget.reserve(100, 100)
    assert capped.value.code == "model_budget_exhausted"
    assert budget.snapshot() == {"limit": 1_000_000}
