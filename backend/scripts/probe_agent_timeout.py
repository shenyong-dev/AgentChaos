"""One explicitly budgeted real request attempt with a controlled 10ms client timeout."""

import json
import time
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage
from redis import Redis

from app.agents.budget import AgentBudget
from app.agents.errors import AgentError
from app.agents.gateway import ModelGateway
from app.core.config import PROJECT_ROOT, Settings


def main():
    settings = Settings()
    redis = Redis.from_url(settings.redis_url, socket_timeout=15)
    root = PROJECT_ROOT / "docs/evidence/T04/controlled-timeout"
    gateway = ModelGateway(settings, AgentBudget(redis, settings), audit_directory=root)
    before = gateway.budget.snapshot()
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "scope": "Real request path with a locally controlled 10ms client timeout; "
        "not a provider outage or injected tool fault",
    }
    try:
        gateway.invoke(
            [HumanMessage(content="仅回复连通测试。")], [], deadline=time.monotonic() + 0.01
        )
        result["outcome"] = "unexpectedly_completed"
    except AgentError as exc:
        result.update({"outcome": exc.code, "http_status": exc.status_code})
        assert exc.code == "model_timeout" and exc.status_code == 504
    finally:
        result.update({"budget_before": before, "budget_after": gateway.budget.snapshot()})
        root.mkdir(parents=True, exist_ok=True)
        (root / "summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False))
        redis.close()


if __name__ == "__main__":
    main()
