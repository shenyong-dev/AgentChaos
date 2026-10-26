"""Append T06 V0 only after source-matched real Linux/SSE acceptance. No paid calls."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from redis import Redis

from app.agents.runtime import AgentRuntime
from app.agents.versions import register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.executors.budget import OPEN_AUTHORIZATION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads((args.evidence / "final-report.json").read_text(encoding="utf-8"))
    checks = report["checks"]
    assert all(
        checks[k] is True
        for k in (
            "sse_equals_mysql_replay",
            "resume_after_five",
            "readonly_did_not_call_model",
            "real_ui",
            "no_agent_pass_claimed",
        )
    )
    settings = Settings()
    engine = create_database_engine(settings)
    redis = Redis.from_url(settings.redis_url)
    try:
        runtime = AgentRuntime(engine, redis, settings)
        assert runtime.manifest["config_hash"] == report["run"]["manifest"]["target"]["config_hash"]
        version = register_version(engine, runtime.manifest)
        ledger = {
            k.decode(): int(v)
            for k, v in redis.hgetall(f"ac:t05:budget:{OPEN_AUTHORIZATION}:agent").items()
        }
        evidence = {
            "checked_at": datetime.now(UTC).isoformat(),
            "version_id": version,
            "manifest": runtime.manifest,
            "acceptance": args.evidence.as_posix(),
            "ledger": ledger,
            "team_acceptance": "pending",
            "T07": "not authorized",
            "evaluation": "not implemented; no agent pass claim",
        }
        (PROJECT_ROOT / "docs/evidence/T06/frozen-v0.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "version_id": version,
                    "config_hash": runtime.manifest["config_hash"],
                    "ledger": ledger,
                }
            )
        )
    finally:
        engine.dispose()
        redis.close()


if __name__ == "__main__":
    main()
