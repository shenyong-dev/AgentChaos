import os

import pytest
from redis.exceptions import ResponseError

from app.core.config import Settings
from app.services.health import probe_mysql, probe_redis


def test_ping_alone_cannot_mark_redis_checkpoint_capability_ready(monkeypatch):
    class PingOnlyRedis:
        def ping(self):
            return True

        def info(self, _):
            return {"redis_version": "7.0-test-double"}

        def execute_command(self, *_):
            raise ResponseError("unknown command")

        def close(self):
            pass

    monkeypatch.setattr("app.services.health.Redis.from_url", lambda *_a, **_k: PingOnlyRedis())
    check = probe_redis(Settings(_env_file=None))
    assert check.status == "error"
    assert check.code == "redis_capabilities_missing"
    assert check.capabilities == {"json": False, "search": False}


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("AC_INTEGRATION") != "1", reason="Set AC_INTEGRATION=1 to probe services"
)
def test_configured_services_are_reachable_with_required_capabilities():
    settings = Settings()
    mysql = probe_mysql(settings)
    redis = probe_redis(settings)
    assert mysql.status == "ok", mysql.code
    assert redis.status == "ok", redis.code
    assert redis.capabilities == {"json": True, "search": True}
