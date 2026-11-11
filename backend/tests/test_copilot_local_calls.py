"""Local request opt-in keeps money/usage limits and historical counters intact."""

import os
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import URL

from app.agents.errors import AgentError
from app.copilot.budget import AUTHORIZATION, CopilotBudget, unlimited_calls
from app.copilot.service import CopilotService
from app.core.config import Settings
from app.schemas.copilot import CopilotAsk


def settings(**extra):
    return Settings(_env_file=None).model_copy(
        update={
            "copilot_allow_paid_model_calls": True,
            "copilot_budget_authorization_id": AUTHORIZATION,
            "copilot_budget_usd": 1,
            "deepseek_api_key": SecretStr("synthetic-test-key"),
            "copilot_local_unlimited_calls": True,
            **extra,
        }
    )


@pytest.mark.parametrize(
    "env,expected", [("development", True), ("test", False), ("production", False)]
)
def test_unlimited_counts_are_only_a_development_opt_in(env, expected):
    assert unlimited_calls(settings(app_env=env)) is expected
    assert not unlimited_calls(settings(app_env=env, copilot_local_unlimited_calls=False))


@pytest.fixture
def client():
    if os.getenv("AC_INTEGRATION") != "1":
        pytest.skip("Owned Redis only, no SQL or model calls")
    c = Redis.from_url(Settings().redis_url)
    yield c
    c.close()


@pytest.mark.parametrize("local", [False, True])
def test_unlimited_counts_keep_money_limits_and_require_existing_ledger(client, tmp_path, local):
    budget = CopilotBudget(
        client, settings(copilot_local_unlimited_calls=local), uuid4(), directory=tmp_path
    )
    budget.key = "ac:test:copilot-local-counts:" + uuid4().hex
    budget.session_key = budget.key + ":session"
    budget.calls_key = budget.session_key + ":calls"
    try:
        with pytest.raises(AgentError):
            budget.reserve(100, 100)
        client.hset(budget.key, mapping={"limit": 1000000, "calls_started": 600})
        client.hset(budget.calls_key, mapping={"calls_started": 200, "token_upper": 2000000})
        if not local:
            with pytest.raises(AgentError):
                budget.reserve(100, 100)
            return
        ticket = budget.reserve(100, 100)
        assert int(client.hget(budget.key, "calls_started")) == 601
        budget.unknown(ticket)
        assert int(client.hget(budget.key, "held_upper")) == ticket["upper_microusd"]
        budget.settle(ticket, {"input_tokens": 50, "output_tokens": 20})
        assert int(client.hget(budget.key, "held_upper")) == 0
        client.hset(budget.session_key, "charged_estimate", 100000)
        with pytest.raises(AgentError):
            budget.reserve(100, 100)
        client.hset(budget.session_key, "charged_estimate", 0)
        client.hset(budget.key, "charged_estimate", 1000000)
        with pytest.raises(AgentError):
            budget.reserve(100, 100)
        assert int(client.hget(budget.key, "calls_started")) == 601
    finally:
        keys = list(client.scan_iter(budget.key + "*"))
        if keys:
            client.delete(*keys)


@pytest.mark.parametrize("local", [False, True])
def test_service_keeps_the_same_receipt_after_more_than_twelve_turns(client, local):
    service = CopilotService(
        SimpleNamespace(url=URL.create("mysql+pymysql", database="synthetic-only")),
        client,
        settings(copilot_local_unlimited_calls=local),
        gateway_factory=Mock(),
    )
    service.prefix = "ac:test:copilot-local-turns:" + uuid4().hex
    sid = uuid4()
    record = {
        "session_id": str(sid),
        "turns": 500,
        "context": {"page": "workspace", "catalog_hash": "a" * 64},
        "context_hash": "b" * 64,
    }
    key = service.key(sid)
    client.set(key, "owned-test", ex=60)
    service.load = Mock(return_value=record)
    service.tools_factory = Mock(return_value=SimpleNamespace(invoke=Mock(), sources={}, trace=[]))
    service.run = Mock(side_effect=lambda *args: args[-2].update(status="completed"))
    body = CopilotAsk(request_id=uuid4(), context_hash="b" * 64, question="下一步做什么？")
    try:
        if not local:
            with pytest.raises(AgentError) as error:
                service.ask(1, "binding", sid, body)
            assert error.value.code == "copilot_session_limit"
            service.run.assert_not_called()
        else:
            result = service.ask(1, "binding", sid, body)
            assert result["status"] == "completed" and record["turns"] == 501
            assert service.ask(1, "binding", sid, body) == result
            service.run.assert_called_once()
    finally:
        keys = list(client.scan_iter(service.prefix + "*"))
        if keys:
            client.delete(*keys)
