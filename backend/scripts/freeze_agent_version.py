"""Register the exact validated T04 baseline in the main DB, never edit an old version."""

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation", type=Path, required=True)
    args = parser.parse_args()
    args.validation = args.validation.resolve()
    proof = json.loads((args.validation / "summary.json").read_text(encoding="utf-8"))
    outcomes = {item["case"]: item["status"] for item in proof["results"]}
    assert outcomes == {f"D{i:02d}": "passed" for i in range(1, 11)}, (
        "All ten development dialogues must pass first"
    )
    settings = Settings()
    engine = create_database_engine(settings)
    redis = Redis.from_url(settings.redis_url, socket_timeout=15)
    try:
        runtime = AgentRuntime(engine, redis, settings)
        validated = json.loads((args.validation / "v0-manifest.json").read_text(encoding="utf-8"))
        assert runtime.manifest["config_hash"] == validated["config_hash"], (
            "Code/config/knowledge changed since validation"
        )
        version_id = register_version(engine, runtime.manifest)
        evidence = {
            "checked_at": datetime.now(UTC).isoformat(),
            "version_id": version_id,
            "validation": args.validation.relative_to(PROJECT_ROOT).as_posix(),
            "manifest": runtime.manifest,
            "budget": runtime.budget.snapshot(),
            "team_acceptance": "pending",
            "T05": "not authorized",
        }
        root = PROJECT_ROOT / "docs/evidence/T04"
        (root / "frozen-v0.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "version_id": version_id,
                    "config_hash": runtime.manifest["config_hash"],
                    "budget": runtime.budget.snapshot(),
                }
            )
        )
    finally:
        engine.dispose()
        redis.close()


if __name__ == "__main__":
    main()
