"""Initialize the separately approved T11 generator ledger once; never reset."""

import json
from datetime import UTC, datetime

from redis import Redis

from app.cases.budget import AUTHORIZATION
from app.core.config import PROJECT_ROOT, Settings

s = Settings()
assert s.casegen_allow_paid_model_calls and s.casegen_budget_unlimited
assert s.casegen_budget_authorization_id == AUTHORIZATION
r = Redis.from_url(s.redis_url)
key = f"ac:t11:budget:{AUTHORIZATION}:case_generator"
marker = PROJECT_ROOT / ".local/t11" / f"{AUTHORIZATION}.initialized.json"
if marker.exists() and not r.exists(key):
    raise SystemExit(
        "Ledger lost after initialization; restore audited bytes, never recreate allowance"
    )
created = r.eval(
    "if redis.call('EXISTS',KEYS[1])==1 then return 0 end "
    "redis.call('HSET',KEYS[1],'limit',-1) return 1",
    1,
    key,
)
snapshot = {k.decode(): int(v) for k, v in r.hgetall(key).items()}
assert snapshot["limit"] == -1
if not marker.exists():
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "initialized_at": datetime.now(UTC).isoformat(),
                "authorization": AUTHORIZATION,
                "key": key,
                "initial": snapshot,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
print(json.dumps({"initialized": bool(created), "ledger": snapshot}))
r.close()
