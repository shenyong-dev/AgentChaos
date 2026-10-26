"""Append the current V0 after T05 source-matched Linux acceptance; no paid requests."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from redis import Redis

from app.agents.runtime import AgentRuntime
from app.agents.versions import register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--controls", type=Path, required=True)
    args = parser.parse_args()
    batch = json.loads((args.batch / "final-report.json").read_text(encoding="utf-8"))
    controls = json.loads((args.controls / "final-report.json").read_text(encoding="utf-8"))
    assert batch["checks"]["api_batch_count"] >= 10
    assert batch["checks"]["all_inconclusive_before_T08"]
    assert batch["checks"]["duplicate_delivery_attempts_unchanged"]
    assert len(batch["checks"]["worker_hosts"]) == 2
    expected = {
        "worker_pause_expiry",
        "old_worker_return_rejected",
        "explicit_recovery_same_scope_session",
        "cancel_before_dispatch",
        "cancel_after_commit_preserves_fact",
        "budget_stop_before_network",
        "waiting_deadline_expiration",
    }
    assert set(controls["checks"]) == expected and set(controls["checks"].values()) == {"passed"}
    settings = Settings()
    engine = create_database_engine(settings)
    redis = Redis.from_url(settings.redis_url)
    try:
        runtime = AgentRuntime(engine, redis, settings)
        assert runtime.manifest["config_hash"] == batch["run"]["manifest"]["target"]["config_hash"]
        control_version = json.loads(
            (args.controls / "explicit-recovery.json").read_text(encoding="utf-8")
        )
        assert (
            runtime.manifest["config_hash"] == control_version["manifest"]["target"]["config_hash"]
        )
        version_id = register_version(engine, runtime.manifest)
        ledger_key = f"ac:t05:budget:{settings.executor_budget_authorization_id}:agent"
        ledger = {k.decode(): int(v) for k, v in redis.hgetall(ledger_key).items()}
        evidence = {
            "checked_at": datetime.now(UTC).isoformat(),
            "version_id": version_id,
            "manifest": runtime.manifest,
            "batch": args.batch.as_posix(),
            "controls": args.controls.as_posix(),
            "ledger": ledger,
            "team_acceptance": "pending",
            "T06": "not authorized",
            "evaluation": "not implemented; no agent pass claim",
        }
        (PROJECT_ROOT / "docs/evidence/T05/frozen-v0.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "version_id": version_id,
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
