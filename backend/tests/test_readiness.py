"""Operation boundaries, stale dependencies and strictly read-only budget probes."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from app.schemas.health import ComponentCheck, HealthReport
from app.services import readiness as r


def test_worker_source_identity_includes_prompts_and_lockfile(tmp_path, monkeypatch):
    from app.core import deployment

    for path in ("backend/app/agents/prompt.txt", "backend/uv.lock"):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original", encoding="utf-8")
    monkeypatch.setattr(deployment, "PROJECT_ROOT", tmp_path)
    original = deployment.source_fingerprint()
    (tmp_path / "backend/app/agents/prompt.txt").write_text("new prompt", encoding="utf-8")
    assert deployment.source_fingerprint() != original
    changed_prompt = deployment.source_fingerprint()
    (tmp_path / "backend/uv.lock").write_text("new lock", encoding="utf-8")
    assert deployment.source_fingerprint() != changed_prompt


def settings(**values):
    return Settings(_env_file=None, **values)


def all_checks():
    return [
        r.observation(k, k, "ready", "test", "synthetic observation", "test")
        for k in (
            "api",
            "mysql",
            "schema",
            "redis",
            "checkpoints",
            "version",
            "credential",
            "worker",
            "interactive_budget",
            "executor_budget",
            "casegen_budget",
            "judge_budget",
            "copilot",
        )
    ]


@pytest.mark.parametrize(
    ("dependency", "unaffected"),
    [
        ("worker", ["history", "interactive", "casegen", "judge"]),
        ("checkpoints", ["history", "casegen", "judge"]),
        ("credential", ["history", "offline"]),
    ],
)
def test_matrix_requires_only_dependencies_used_by_that_operation(dependency, unaffected):
    checks = all_checks()
    next(c for c in checks if c.id == dependency).status = "blocked"
    results = {o.id: o for o in r.operations(checks)}
    for operation in unaffected:
        assert results[operation].status == "ready"
    assert results["batch"].status == "blocked"


def test_paid_off_does_not_disable_history_and_copilot_is_blocked(monkeypatch):
    class NoRedisIO:
        def close(self):
            pass

    monkeypatch.setattr(r.Redis, "from_url", lambda *a, **k: NoRedisIO())
    budgets = r.budget_checks(settings())
    assert all(c.code == "paid_switch_off" for c in budgets)
    assert all(c.facts["ledger_checked"] is False for c in budgets)
    checks = [c for c in all_checks() if c.id not in {b.id for b in budgets}] + budgets
    operations = {o.id: o for o in r.operations(checks)}
    assert operations["history"].status == "ready"
    assert operations["judge"].status == "blocked"
    assert operations["copilot"].status == "blocked"


@pytest.mark.parametrize("calls,expected", [(34, "ready"), (60, "blocked")])
def test_copilot_readiness_uses_original_ledger_without_writes(monkeypatch, calls, expected):
    from app.copilot.budget import AUTHORIZATION

    reads = []

    class ReadOnlyRedis:
        def hgetall(self, key):
            reads.append(key)
            return {
                b"limit": b"1000000",
                b"calls_started": str(calls).encode(),
                b"held_upper": b"0",
            }

        def close(self):
            pass

    monkeypatch.setattr(r.Redis, "from_url", lambda *a, **k: ReadOnlyRedis())
    results = r.budget_checks(
        settings(
            copilot_allow_paid_model_calls=True,
            copilot_budget_authorization_id=AUTHORIZATION,
            copilot_budget_usd=1,
        )
    )
    value = next(c for c in results if c.id == "copilot")
    assert value.status == expected
    assert reads == [f"ac:t17e:budget:{AUTHORIZATION}:copilot"]
    checks = [c for c in all_checks() if c.id not in {b.id for b in results}] + results
    assert next(o for o in r.operations(checks) if o.id == "copilot").status == expected


@pytest.mark.parametrize(
    ("ledger", "expected"),
    [
        ({}, "budget_ledger_missing"),
        ({"limit": 500000, "calls_started": 12}, "budget_exhausted"),
        ({"limit": 500000, "charged_estimate": 100000, "held_upper": 400000}, "budget_exhausted"),
        ({"limit": 500000, "charged_estimate": 1, "calls_started": 11}, "budget_configured"),
        ({"limit": -1}, "budget_protocol_error"),
        ({"limit": 500000, "protocol_overrun": 1}, "budget_protocol_error"),
        ({"limit": 500000, "held_upper": -1}, "budget_protocol_error"),
    ],
)
def test_budget_probe_does_not_initialize_reserve_or_reset(ledger, expected, monkeypatch):
    reads = []

    class ReadOnlyRedis:
        def hgetall(self, key):
            reads.append(key)
            return {k.encode(): str(v).encode() for k, v in ledger.items()}

        def close(self):
            pass

    monkeypatch.setattr(r.Redis, "from_url", lambda *a, **k: ReadOnlyRedis())
    result = {
        c.id: c
        for c in r.budget_checks(
            settings(
                judge_allow_paid_model_calls=True,
                judge_budget_authorization_id="T17B-20261030-judge-050",
                judge_budget_usd=0.5,
                judge_max_output_tokens=1536,
            )
        )
    }["judge_budget"]
    assert result.code == expected
    assert len(reads) == 1


def test_budget_read_failure_is_unknown(monkeypatch):
    class UnreachableRedis:
        def hgetall(self, key):
            raise RuntimeError("never-echo-password")

        def close(self):
            pass

    monkeypatch.setattr(r.Redis, "from_url", lambda *a, **k: UnreachableRedis())
    result = r.budget_checks(
        settings(
            allow_paid_model_calls=True,
            model_budget_authorization_id="T04-20261025-agent-1",
            agent_budget_usd=1,
        )
    )[0]
    assert result.status == "unknown"
    assert "never-echo" not in result.model_dump_json()


@pytest.mark.parametrize(
    "mismatch", [None, "source_hash", "database_binding", "execution_hash", "queues", "no_reply"]
)
def test_worker_checks_actual_binding_source_config_and_consumer_queue(monkeypatch, mismatch):
    s = settings()
    response = {
        "protocol": "T17C-1",
        "source_hash": "source",
        "database_binding": "db",
        "execution_hash": "execution",
        "queues": [s.executor_queue],
    }
    if mismatch not in (None, "no_reply"):
        response[mismatch] = "different"
    calls = []

    def broadcast(command, **kwargs):
        calls.append(command)
        assert kwargs == {"reply": True, "timeout": 2}
        return [] if mismatch == "no_reply" else [{"worker": response}]

    app = SimpleNamespace(
        conf=SimpleNamespace(update=lambda **k: None),
        control=SimpleNamespace(broadcast=broadcast),
        close=lambda: None,
    )
    monkeypatch.setattr(r, "Celery", lambda *a, **k: app)
    monkeypatch.setattr(r, "database_binding", lambda _: "db")
    monkeypatch.setattr(r, "source_fingerprint", lambda: "source")
    monkeypatch.setattr(r, "execution_fingerprint", lambda _: "execution")
    observed = r.worker_check(s)
    assert observed.status == (
        "ready" if mismatch is None else "unknown" if mismatch == "no_reply" else "blocked"
    )
    assert calls == ["agentchaos_readiness"]
    assert "worker" not in observed.facts


def test_partial_api_report_skips_unreachable_dependencies_and_never_exposes_secrets(monkeypatch):
    s = settings(
        mysql_password="db-sensitive",
        redis_password="redis-sensitive",
        deepseek_api_key="provider-sensitive",
    )

    async def health(_):
        return HealthReport(
            status="degraded",
            checked_at=datetime.now(UTC),
            checks=[
                ComponentCheck(
                    name=k, status="ok" if k == "api" else "error", code=k, message="unavailable"
                )
                for k in ("api", "mysql", "redis")
            ],
        )

    def forbidden(_):
        raise AssertionError("Dependent probe must be skipped")

    monkeypatch.setattr(r, "collect_health", health)
    monkeypatch.setattr(r, "database_checks", forbidden)
    monkeypatch.setattr(r, "worker_check", forbidden)
    monkeypatch.setattr(
        r, "budget_checks", lambda _: [c for c in all_checks() if c.id.endswith("budget")]
    )
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: s
    with TestClient(application) as client:
        response = client.get("/api/v1/system/readiness")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()["data"]
    assert (
        next(c for c in result["checks"] if c["id"] == "worker")["code"] == "worker_probe_skipped"
    )
    assert all(
        secret not in response.text
        for secret in ("db-sensitive", "redis-sensitive", "provider-sensitive")
    )
    assert datetime.fromisoformat(result["expires_at"]) > datetime.fromisoformat(
        result["checked_at"]
    )


def test_redis_missing_modules_preserves_connection_and_history(monkeypatch):
    async def health(_):
        return HealthReport(
            status="degraded",
            checked_at=datetime.now(UTC),
            checks=[
                ComponentCheck(
                    name=k,
                    status="ok" if k != "redis" else "error",
                    code="redis_capabilities_missing" if k == "redis" else k,
                    message="test",
                )
                for k in ("api", "mysql", "redis")
            ],
        )

    monkeypatch.setattr(r, "collect_health", health)
    monkeypatch.setattr(r, "database_checks", lambda _: all_checks()[2:3] + [all_checks()[5]])
    monkeypatch.setattr(r, "worker_check", lambda _: all_checks()[7])
    monkeypatch.setattr(r, "budget_checks", lambda _: all_checks()[8:12])
    report = asyncio.run(r.collect_readiness(settings()))
    checks = {c.id: c for c in report.checks}
    assert checks["redis"].status == "ready"
    assert checks["checkpoints"].status == "blocked"
    assert next(o for o in report.operations if o.id == "history").status == "ready"
