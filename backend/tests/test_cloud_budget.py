"""Synthetic settings + owned Redis controls, no provider calls or main ledgers."""

import json
import os
from uuid import uuid4

import pytest
from redis import Redis

from app.agents.budget import AgentBudget
from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.cases.budget import GenerationBudget
from app.copilot.budget import CopilotBudget
from app.core import cloud, cloud_init
from app.core.config import Settings
from app.interactive.budget import InteractionBudget
from app.judging.budget import JudgeBudget


@pytest.fixture
def policy(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cloud_init, "PROJECT_ROOT", tmp_path)
    approval = {
        "authorization": cloud.AUTHORIZATION,
        "deployment_id": uuid4().hex,
        "roles": list(cloud.ROLES),
        "cumulative_money_limit": None,
        "cumulative_request_limit": None,
        "approved_statement": "允许真实调用，不设累计费用上限",
    }
    path = tmp_path / ".local/cloud/approval.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(approval), encoding="utf-8")
    values = {
        "app_env": "production",
        "cloud_deployment_id": approval["deployment_id"],
        "cloud_approval_sha256": canonical_hash(approval),
        "cloud_open_model_budget": True,
        **{field: cloud.AUTHORIZATION for field in cloud.FIELDS.values()},
        "executor_budget_authorization_id": cloud.AUTHORIZATION,
        "allow_paid_model_calls": True,
        "casegen_allow_paid_model_calls": True,
        "judge_allow_paid_model_calls": True,
        "copilot_allow_paid_model_calls": True,
        "casegen_budget_unlimited": True,
        "judge_budget_unlimited": True,
        "executor_agent_budget_unlimited": True,
    }
    return Settings(_env_file=None, **values), path.with_name("initialization.json")


@pytest.fixture
def redis(policy):
    if os.getenv("AC_INTEGRATION") != "1":
        pytest.skip("Owned Redis integration only")
    client = Redis.from_url(Settings().redis_url, socket_timeout=3)
    prefix = f"ac:cloud:{policy[0].cloud_deployment_id}:"
    yield client
    keys = list(client.scan_iter(prefix + "*"))
    if keys:
        client.delete(*keys)
    client.close()


def test_cloud_needs_exact_separate_proof_and_production(policy):
    settings, _ = policy
    assert cloud.team_cloud(settings)
    for extra in (
        {"app_env": "development"},
        {"cloud_approval_sha256": "0" * 64},
        {"cloud_open_model_budget": False},
        {"cloud_deployment_id": uuid4().hex},
    ):
        assert not cloud.team_cloud(settings.model_copy(update=extra))
    assert not cloud.team_cloud(Settings(_env_file=None))
    assert not cloud.authorized(settings, "unknown")
    assert not cloud.authorized(
        settings.model_copy(update={"copilot_budget_authorization_id": "old"}), "copilot"
    )


def test_restart_verifies_preserves_unknown_and_never_reinitializes(redis, policy):
    settings, marker = policy
    assert cloud_init.initialize(redis, settings, marker)["initialized"]
    root = cloud.root_key(settings, "agent")
    redis.hset(root, mapping={"charged_estimate": 456, "held_upper": 111, "calls_unknown": 1})
    assert cloud_init.initialize(redis, settings, marker)["verified_existing"]
    assert redis.hget(root, "held_upper") == b"111"
    redis.delete(root)
    with pytest.raises(RuntimeError, match="never reset"):
        cloud_init.initialize(redis, settings, marker)
    assert not redis.exists(root)


def test_durable_intent_before_failed_init_is_not_fresh_credit(redis, policy, monkeypatch):
    settings, marker = policy
    monkeypatch.setattr(redis, "eval", lambda *args: (_ for _ in ()).throw(OSError("lost reply")))
    with pytest.raises(OSError):
        cloud_init.initialize(redis, settings, marker)
    assert marker.exists()
    with pytest.raises(RuntimeError, match="never reset"):
        cloud_init.initialize(redis, settings, marker)


@pytest.mark.parametrize("role", cloud.ROLES)
def test_cloud_accounts_without_aggregate_caps_and_missing_root_blocks(
    redis, policy, tmp_path, role
):
    settings, marker = policy
    cloud_init.initialize(redis, settings, marker)
    if role == "agent":
        budget = InteractionBudget(redis, settings, uuid4(), directory=tmp_path)
        assert AgentBudget(redis, settings).key == budget.key
    elif role == "copilot":
        budget = CopilotBudget(redis, settings, uuid4(), directory=tmp_path)
    else:
        cls = GenerationBudget if role == "case_generator" else JudgeBudget
        budget = cls(redis, settings, uuid4().hex, lambda *args: None)
    assert budget.key == cloud.root_key(settings, role)
    redis.hset(budget.key, mapping={"charged_estimate": 900000000, "calls_started": 999999})
    cloud_init.verify_ledger(redis, settings, role)
    ticket = budget.reserve(100, 100)
    budget.unknown(ticket)
    assert int(redis.hget(budget.key, "held_upper")) == ticket["upper_microusd"]
    budget.settle(ticket, {"input_tokens": 50, "output_tokens": 20})
    assert redis.hget(budget.key, "held_upper") == b"0"
    redis.delete(budget.key)
    with pytest.raises(AgentError):
        budget.reserve(100, 100)
    assert not redis.exists(budget.key)


def test_global_concurrency_lock_does_not_overwrite_or_unlock_other_request(
    redis, policy, monkeypatch
):
    settings, marker = policy
    cloud_init.initialize(redis, settings, marker)
    monkeypatch.setattr(cloud, "require_capacity", lambda s: None)
    key = f"ac:cloud:{settings.cloud_deployment_id}:model-inflight"
    with cloud.invocation_guard(redis, settings, "agent"):
        with pytest.raises(AgentError, match="已有模型请求"):
            with cloud.invocation_guard(redis, settings, "copilot"):
                pytest.fail("Concurrent request was admitted")
        redis.set(key, "other-request")
    assert redis.get(key) == b"other-request"


def test_missing_continuity_prevents_invocation(redis, policy, monkeypatch):
    settings, marker = policy
    cloud_init.initialize(redis, settings, marker)
    redis.delete(f"ac:cloud:{settings.cloud_deployment_id}:initialization")
    monkeypatch.setattr(cloud, "require_capacity", lambda s: pytest.fail("Should stop first"))
    with pytest.raises(AgentError, match="用量记录"):
        with cloud.invocation_guard(redis, settings, "agent"):
            pytest.fail("Missing continuity was admitted")
