"""One-time cloud ledgers. Existing or uncertain initialization never resets usage."""

import json
import os
from pathlib import Path
from uuid import uuid4

from redis import Redis

from app.core.cloud import ROLES, root_key, team_cloud
from app.core.config import PROJECT_ROOT, get_settings

INIT = """
for i=1,#KEYS do
 if redis.call('EXISTS',KEYS[i])~=0 then return 0 end
end
for i=2,#KEYS do
 redis.call('HSET',KEYS[i],'limit',-1,'charged_estimate',0,'held_upper',0,
            'calls_started',0,'calls_completed',0,'calls_unknown',0,'token_upper',0)
end
redis.call('SET',KEYS[1],ARGV[1])
return 1
"""


def initialize(redis, settings, marker: Path):
    if not team_cloud(settings):
        raise RuntimeError("Matching explicit team-cloud approval is required")
    roots = [root_key(settings, role) for role in ROLES]
    if not all(roots):
        raise RuntimeError("Every role must match this cloud authorization")
    binding = f"ac:cloud:{settings.cloud_deployment_id}:initialization"
    expected = {
        "deployment_id": settings.cloud_deployment_id,
        "approval_sha256": settings.cloud_approval_sha256,
        "roots": roots,
    }
    if marker.exists():
        saved = json.loads(marker.read_bytes())
        if any(saved.get(k) != v for k, v in expected.items()):
            raise RuntimeError("Cloud initialization binding changed; inspect original records")
        token = redis.get(binding)
        if token is None or token.decode() != saved["nonce"]:
            raise RuntimeError("Cloud ledger binding missing; restore original Redis, never reset")
        if any(redis.hget(key, "limit") != b"-1" for key in roots):
            raise RuntimeError("Cloud ledger missing or policy changed; restore, never reset")
        return {"initialized": False, "verified_existing": True}
    if redis.exists(binding, *roots):
        raise RuntimeError(
            "Unexpected cloud ledger without local marker; inspect before continuing"
        )
    marker.parent.mkdir(parents=True, exist_ok=True)
    saved = {**expected, "nonce": uuid4().hex}
    # Create-only durable local intent precedes Redis. A crash is not permission
    # to issue fresh ledgers; a retry must prove the original remote binding.
    with marker.open("x", encoding="utf-8") as stream:
        json.dump(saved, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    if redis.eval(INIT, len(roots) + 1, binding, *roots, saved["nonce"]) != 1:
        raise RuntimeError("Cloud initialization uncertain; inspect original intent and Redis")
    return {"initialized": True, "verified_existing": False}


def verify_ledger(redis, settings, role):
    if not settings.cloud_deployment_id:
        return
    marker = PROJECT_ROOT / ".local/cloud/initialization.json"
    if not team_cloud(settings) or root_key(settings, role) is None:
        raise RuntimeError("Cloud role authorization is unavailable")
    # This path never calls initialize and never creates or updates a ledger.
    saved = json.loads(marker.read_bytes())
    key = root_key(settings, role)
    token = redis.get(f"ac:cloud:{settings.cloud_deployment_id}:initialization")
    if (
        saved.get("deployment_id") != settings.cloud_deployment_id
        or saved.get("approval_sha256") != settings.cloud_approval_sha256
        or key not in saved.get("roots", [])
        or token is None
        or token.decode() != saved.get("nonce")
        or redis.hget(key, "limit") != b"-1"
    ):
        raise RuntimeError("Cloud ledger continuity is unavailable; restore original records")


if __name__ == "__main__":
    settings = get_settings()
    client = Redis.from_url(settings.redis_url, socket_timeout=5, socket_connect_timeout=5)
    try:
        print(
            json.dumps(
                initialize(client, settings, PROJECT_ROOT / ".local/cloud/initialization.json")
            )
        )
    finally:
        client.close()
