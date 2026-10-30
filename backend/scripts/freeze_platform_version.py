"""Append a source-compatible main V0 after T17; no model invocation or run."""

import json
from datetime import UTC, datetime

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.models import TargetVersion


def main():
    output = PROJECT_ROOT / "docs/evidence/T17/main-version.json"
    assert not output.exists()
    s = Settings()
    assert not any(
        (
            s.allow_paid_model_calls,
            s.executor_allow_paid_model_calls,
            s.casegen_allow_paid_model_calls,
            s.judge_allow_paid_model_calls,
        )
    )
    engine = create_database_engine(s)
    redis = Redis.from_url(s.redis_url)
    with Session(engine) as session:
        before = list(session.scalars(select(TargetVersion.id).order_by(TargetVersion.id)))
    runtime = AgentRuntime(engine, redis, s, auto_register=True)
    assert runtime.version_id not in before
    record = {
        "checked_at": datetime.now(UTC).isoformat(),
        "version_id": runtime.version_id,
        "config_hash": runtime.manifest["config_hash"],
        "old_version_ids": before,
        "kind": "source-compatible V0; platform fixes only",
        "new_model_calls": 0,
        "T16_frozen_versions_or_results_changed": False,
    }
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False))
    redis.close()
    engine.dispose()


if __name__ == "__main__":
    main()
