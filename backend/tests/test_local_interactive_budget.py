"""Local-only policy checks and real Redis reservations; no model requests."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from redis import Redis

from app.agents.errors import AgentError
from app.core.config import Settings
from app.interactive.budget import (
    AUTHORIZATION,
    LOCAL_AUTHORIZATION,
    InteractionBudget,
    authorized,
    limits,
)


def local_settings(**extra):
    return Settings().model_copy(
        update={
            "app_env": "development",
            "allow_paid_model_calls": True,
            "interactive_local_unlimited": True,
            "model_budget_authorization_id": LOCAL_AUTHORIZATION,
            **extra,
        }
    )


@pytest.mark.parametrize("environment", ["test", "production"])
def test_open_local_policy_does_not_authorize_other_environments(environment):
    settings = local_settings(app_env=environment)
    assert not authorized(settings)
    assert limits(settings)["session_calls"] == 20


def test_policy_requires_explicit_opt_in_matching_authorization_and_model():
    assert authorized(local_settings())
    assert limits(local_settings())["session_calls"] is None
    for change in (
        {"interactive_local_unlimited": False},
        {"model_budget_authorization_id": AUTHORIZATION},
        {"allow_paid_model_calls": False},
        {"agent_thinking": "enabled"},
    ):
        assert not authorized(local_settings(**change))


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="Scoped real Redis required")
def test_local_accounting_exceeds_old_caps_retains_unknown_and_blocks_corruption(tmp_path):
    settings = local_settings()
    redis = Redis.from_url(settings.redis_url)
    budget = InteractionBudget(redis, settings, uuid4(), directory=tmp_path)
    original_key = budget.key
    budget.key = "ac:local:testbudget:" + uuid4().hex
    budget.session_key = budget.key + ":session"
    budget.calls_key = budget.session_key + ":calls"
    try:
        with pytest.raises(AgentError):
            budget.reserve(1, 1)
        redis.hset(budget.key, "limit", 1_000_000)
        with pytest.raises(AgentError):
            budget.reserve(1, 1)
        redis.hset(
            budget.key, mapping={"limit": -1, "calls_started": 500, "charged_estimate": 2_000_000}
        )
        redis.hset(budget.session_key, mapping={"calls_started": 200, "charged_estimate": 150_000})
        redis.hset(budget.calls_key, mapping={"calls_started": 200, "token_upper": 2_000_000})
        with ThreadPoolExecutor(max_workers=4) as pool:
            tickets = list(pool.map(lambda _: budget.reserve(100, 1024), range(8)))
        assert budget.snapshot()["calls_started"] == 208
        assert int(redis.hget(budget.key, "calls_started")) == 508
        budget.unknown(tickets[0])
        assert budget.snapshot()["calls_unknown"] == 1
        for ticket in tickets:
            budget.settle(ticket, {"input_tokens": 10, "output_tokens": 10})
        assert budget.snapshot()["held_upper"] == 0
        assert budget.snapshot()["calls_unknown"] == 0
        before = budget.snapshot()
        budget.settle(tickets[0], {"input_tokens": 10, "output_tokens": 10})
        assert budget.snapshot() == before  # Replay does not charge twice.
        for prompt, output in ((65_537, 1), (1, 1025)):
            with pytest.raises(AgentError):
                budget.reserve(prompt, output)
        redis.hset(budget.key, "protocol_overrun", 1)
        with pytest.raises(AgentError):
            budget.reserve(1, 1)
        assert "ac:local:interactive:budget:" in original_key
    finally:
        keys = list(redis.scan_iter(budget.key + "*"))
        if keys:
            redis.delete(*keys)
        redis.close()
