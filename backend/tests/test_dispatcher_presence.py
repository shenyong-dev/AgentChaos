"""Recent success and matching bindings are required; a heartbeat alone is not ready."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.services import readiness as r


@pytest.mark.parametrize(
    "condition",
    ["valid", "expired", "future", "failed", "source", "database", "queue", "persistent", "broken"],
)
def test_dispatcher_presence_cannot_turn_stale_or_mismatched_state_green(monkeypatch, condition):
    value = {
        "protocol": "T17H-1",
        "source_hash": "source",
        "database_binding": "database",
        "execution_hash": "execution",
        "queue": "agentchaos",
        "last_scan": datetime.now(UTC).isoformat(),
        "scan_ok": True,
    }
    ttl = 8
    if condition == "expired":
        value["last_scan"] = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    elif condition == "future":
        value["last_scan"] = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
    elif condition == "failed":
        value["scan_ok"] = False
    elif condition in ("source", "database"):
        value[condition + ("_hash" if condition == "source" else "_binding")] = "other"
    elif condition == "queue":
        value["queue"] = "other"
    elif condition == "persistent":
        ttl = -1

    class ReadOnly:
        def scan_iter(self, pattern, **kwargs):
            assert pattern.endswith(":dispatcher:presence:*")
            return iter([b"controlled"])

        def get(self, _):
            return b"invalid-json" if condition == "broken" else json.dumps(value).encode()

        def ttl(self, _):
            return ttl

        def close(self):
            pass

    monkeypatch.setattr(r.Redis, "from_url", lambda *a, **k: ReadOnly())
    monkeypatch.setattr(r, "source_fingerprint", lambda: "source")
    monkeypatch.setattr(r, "database_binding", lambda _: "database")
    monkeypatch.setattr(r, "execution_fingerprint", lambda _: "execution")
    result = r.dispatcher_check(Settings(_env_file=None))
    assert result.status == ("ready" if condition == "valid" else "blocked")
    assert result.facts["matching_dispatchers"] == (1 if condition == "valid" else 0)
