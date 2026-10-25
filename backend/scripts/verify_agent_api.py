"""Real HTTP session smoke. --live-message explicitly spends the existing T04 agent cap."""

import argparse
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from redis import Redis

from app.agents.budget import AgentBudget
from app.core.config import PROJECT_ROOT, Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-message", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    redis = Redis.from_url(settings.redis_url, socket_timeout=15)
    budget = AgentBudget(redis, settings)
    result = {"checked_at": datetime.now(UTC).isoformat(), "budget_before": budget.snapshot()}
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=130) as client:
        result["unauthenticated_create"] = client.post("/api/v1/demo/sessions", json={}).status_code
        assert result["unauthenticated_create"] == 401
        client.auth = ("demo_operator", settings.demo_seed_password.get_secret_value())
        result["scope_injection"] = client.post(
            "/api/v1/demo/sessions", json={"scope_id": "other"}
        ).status_code
        assert result["scope_injection"] == 400
        response = client.post("/api/v1/demo/sessions", json={})
        assert response.status_code == 201, response.status_code
        view = response.json()["data"]
        result["created"] = view
        url = "/api/v1/demo/sessions/" + view["session_id"]
        assert client.get(url).json()["data"]["config_hash"] == view["config_hash"]
        assert client.get(url + "/history").json()["data"]["items"] == []
        if args.live_message:
            response = client.post(
                url + "/messages",
                json={
                    "request_id": str(uuid4()),
                    "expected_revision": 0,
                    "text": "请查实验规则中的押金收费金额，只回答有证据的信息，不进行预约。",
                },
            )
            assert response.status_code == 200, response.status_code
            view = response.json()["data"]
            assert view["pending_draft"] is None and view["business_result"] is None
            assert view["last_retrieval"]["status"] in ("no_lexical_match", "low_relevance")
            result["real_message_response"] = view
        else:
            assert budget.snapshot() == result["budget_before"], (
                "Read/create smoke must not call a model"
            )
        result["history"] = client.get(url + "/history").json()["data"]
        result["health"] = client.get("/api/v1/health").json()["data"]
        result["public_config"] = client.get("/api/v1/system/config").json()["data"]
    result["budget_after"] = budget.snapshot()
    root = PROJECT_ROOT / "docs/evidence/T04"
    (root / "http-smoke.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    redis.close()
    print(
        json.dumps(
            {
                "http_smoke": "passed",
                "live_message": args.live_message,
                "session_id": view["session_id"],
                "budget": result["budget_after"],
            }
        )
    )


if __name__ == "__main__":
    main()
