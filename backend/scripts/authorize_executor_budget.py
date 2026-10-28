"""One-time local initialization for the explicitly approved T05 cap; never reset."""

import argparse
import json
from datetime import UTC, datetime

from redis import Redis

from app.core.config import PROJECT_ROOT, Settings
from app.executors.budget import (
    AUTHORIZATION,
    DIALOGUE_AUTHORIZATION,
    FAULT_AUTHORIZATION,
    OPEN_AUTHORIZATION,
    authorization_limit,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorization",
        required=True,
        choices=[AUTHORIZATION, OPEN_AUTHORIZATION, FAULT_AUTHORIZATION, DIALOGUE_AUTHORIZATION],
    )
    args = parser.parse_args()
    settings = Settings()
    if (
        not settings.executor_allow_paid_model_calls
        or settings.executor_budget_authorization_id != args.authorization
        or authorization_limit(settings) == 0
    ):
        raise SystemExit("Local settings do not match this approved authorization")
    redis = Redis.from_url(settings.redis_url)
    key = f"ac:t05:budget:{args.authorization}:agent"
    marker = PROJECT_ROOT / ".local/t05" / f"{args.authorization}.initialized.json"
    if marker.exists() and not redis.exists(key):
        raise SystemExit(
            "Ledger is missing after initialization. Restore audited ledger; do not reset."
        )
    result = redis.eval(
        """
if redis.call('EXISTS',KEYS[1])==1 then return 0 end
redis.call('HSET',KEYS[1],'limit',ARGV[1])
return 1
""",
        1,
        key,
        authorization_limit(settings),
    )
    snapshot = {k.decode(): int(v) for k, v in redis.hgetall(key).items()}
    if not marker.exists():
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "initialized_at": datetime.now(UTC).isoformat(),
                    "authorization": args.authorization,
                    "ledger_key": key,
                    "initial": snapshot,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(json.dumps({"newly_initialized": result == 1, "ledger": snapshot}))


if __name__ == "__main__":
    main()
