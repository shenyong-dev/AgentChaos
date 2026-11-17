"""Real isolated Redis accounting controls; no supplier or original ledger mutation."""

from uuid import uuid4

import pytest
from redis import Redis

from app.agents.errors import AgentError
from app.core.config import Settings
from benchmarks.p04_trial import budget as b


@pytest.fixture
def account(tmp_path, monkeypatch):
    redis = Redis.from_url(Settings().redis_url, decode_responses=True)
    redis.ping()
    key = "ac:p04-control:" + uuid4().hex + ":budget:copilot"
    monkeypatch.setattr(b, "ROOT", tmp_path)
    monkeypatch.setattr(b, "LEDGER", key)
    monkeypatch.setattr(b, "approval", lambda package: {"package_hash": "1" * 64})
    monkeypatch.setattr(b, "check", lambda *args: None)
    settings = Settings().model_copy(
        update={
            "copilot_budget_authorization_id": b.AUTHORIZATION,
            "copilot_allow_paid_model_calls": True,
            "copilot_local_unlimited_calls": True,
            "copilot_model": "deepseek-flash",
            "copilot_thinking": "disabled",
            "copilot_max_output_tokens": 1536,
            "model_timeout_seconds": 45,
        }
    )
    b.initialize(redis, "explicit-mechanical-control")
    ledger = b.PilotBudget(redis, settings, str(uuid4()), "explicit-mechanical-control", None)
    try:
        yield redis, key, ledger, tmp_path
    finally:
        keys = list(redis.scan_iter(match=key + "*"))
        assert all(value.startswith(key) for value in keys)
        if keys:
            redis.delete(*keys)


def test_unlimited_keeps_usage_and_unknown_reservations(account):
    redis, key, ledger, _ = account
    ticket = ledger.reserve(32, 32)
    assert int(redis.hget(key, "limit")) == -1
    ledger.settle(ticket, {"input_tokens": 8, "output_tokens": 4})
    unknown = ledger.reserve(32, 32)
    ledger.unknown(unknown)
    with pytest.raises(AgentError, match="未知"):
        ledger.reserve(32, 32)
    assert int(redis.hget(key, "calls_started")) == 2
    assert int(redis.hget(key, "held_upper")) > 0
    ledger.settle(unknown, {"input_tokens": 5, "output_tokens": 3})
    assert int(redis.hget(key, "calls_unknown")) == 0
    assert int(redis.hget(key, "held_upper")) == 0
    assert int(redis.hget(key, "charged_estimate")) > 0


def test_missing_ledger_does_not_reissue_authorization(account):
    redis, key, _, folder = account
    marker = (folder / "initialization.json").read_bytes()
    redis.delete(key)
    with pytest.raises(ValueError, match="never reinitialize"):
        b.initialize(redis, "explicit-mechanical-control")
    assert not redis.exists(key)
    assert (folder / "initialization.json").read_bytes() == marker


def test_request_bounds_and_redis_identity_remain_binding(account, monkeypatch):
    redis, _, ledger, _ = account
    with pytest.raises(AgentError, match="上下文"):
        ledger.reserve(32769, 1536)
    original = redis.info
    monkeypatch.setattr(
        redis,
        "info",
        lambda *args, **kwargs: {
            **original(*args, **kwargs),
            "run_id": "different-mechanical-instance",
        },
    )
    with pytest.raises(ValueError, match="identity changed"):
        ledger.reserve(32, 32)
