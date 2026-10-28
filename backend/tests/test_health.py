from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.main import create_app
from app.schemas.health import ComponentCheck


@pytest.fixture
def client():
    application = create_app()
    settings = Settings(
        _env_file=None,
        app_env="test",
        mysql_password=SecretStr("test-db-secret"),
        redis_password=SecretStr("test-redis-secret"),
        deepseek_api_key=SecretStr("test-model-secret"),
    )
    application.dependency_overrides[get_settings] = lambda: settings
    with TestClient(application) as test_client:
        yield test_client


def test_liveness_is_independent_of_dependencies_and_correlates_requests(client):
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "not-a-uuid"})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "alive"
    request_id = response.json()["request_id"]
    assert str(UUID(request_id)) == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"


def test_readiness_fails_when_a_required_dependency_is_unavailable(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.health.probe_mysql",
        lambda _: ComponentCheck(
            name="mysql", status="ok", code="mysql_connected", message="connected"
        ),
    )
    monkeypatch.setattr(
        "app.services.health.probe_redis",
        lambda _: ComponentCheck(
            name="redis", status="error", code="redis_unreachable", message="not connected"
        ),
    )
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json()["data"]["status"] == "degraded"
    model = next(c for c in response.json()["data"]["checks"] if c["name"] == "model")
    assert model["status"] == "not_probed"


def test_public_config_never_exposes_credentials_or_claims_connectivity(client):
    response = client.get("/api/v1/system/config")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["model_key_configured"] is True
    assert body["model_connectivity"] == "not_probed"
    assert body["test_execution_available"] is True
    assert body["assertion_evaluation_available"] is True
    assert body["executor_liveness"] == "not_probed"
    assert {r["role"] for r in body["roles"]} == {"agent", "case_generator", "judge"}
    for secret in ("test-db-secret", "test-redis-secret", "test-model-secret"):
        assert secret not in response.text


def test_run_endpoint_requires_authentication_and_has_uniform_error(client):
    response = client.post("/api/v1/runs")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "authentication_required"
    assert body["request_id"] == response.headers["x-request-id"]


def test_unexpected_failure_does_not_echo_sensitive_exception(client, monkeypatch):
    async def broken_probe(_):
        raise RuntimeError("driver credential: never-return-this-secret")

    monkeypatch.setattr("app.api.routes.collect_health", broken_probe)
    response = client.get("/api/v1/health")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "never-return-this-secret" not in response.text
