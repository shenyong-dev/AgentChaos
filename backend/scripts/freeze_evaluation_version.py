"""Freeze the source-matched T08 implementation after offline real-storage acceptance."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from redis import Redis

from app.agents.versions import register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.evaluation.engine import evaluator_manifest
from app.executors.service import RunService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    directory = args.evidence.resolve()
    assert directory.is_relative_to((PROJECT_ROOT / "docs/evidence/T08").resolve())
    report = json.loads((directory / "matrix.json").read_bytes())
    assert report["mode"] == "offline_gateway_real_SQL_Redis_tools"
    assert len(report["scenarios"]) == 11 and report["provider_requests"] == 0
    assert "11 passed" in (directory / "playwright.txt").read_text(encoding="utf-8")
    settings = Settings()
    assert not settings.allow_paid_model_calls and not settings.executor_allow_paid_model_calls
    redis = Redis.from_url(settings.redis_url)
    expected = json.loads((PROJECT_ROOT / "docs/evidence/T08/preparation.json").read_bytes())[
        "old_ledgers"
    ]
    assert {
        k: {f.decode(): int(v) for f, v in redis.hgetall(k).items()} for k in expected
    } == expected
    engine = create_database_engine(settings)
    try:
        manifest, evaluator = RunService(engine, settings).current_manifest(), evaluator_manifest()
        for kind, result in report["scenarios"].items():
            snapshot = json.loads((directory / f"{kind}-run.json").read_bytes())
            assert snapshot["manifest"]["target"]["config_hash"] == manifest["config_hash"]
            assert snapshot["manifest"]["evaluation"] == evaluator
            assert snapshot["executions"][0]["timing"]["gateway_mode"] == "offline_test_double"
            assert result["actual"]["verdict"] == result["expected_verdict"]
        version = register_version(engine, manifest)
        out = {
            "checked_at": datetime.now(UTC).isoformat(),
            "version_id": version,
            "manifest": manifest,
            "evaluator": evaluator,
            "evidence": directory.relative_to(PROJECT_ROOT).as_posix(),
            "new_provider_requests": 0,
            "unchanged_old_ledgers": expected,
            "team_acceptance": "pending",
            "T09": "not authorized",
            "validation_boundary": (
                "New implementation: real SQL/Redis with explicit offline Agent; "
                "old real-provider traces reevaluated separately"
            ),
        }
        (PROJECT_ROOT / "docs/evidence/T08/frozen-v0.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "version_id": version,
                    "config_hash": manifest["config_hash"],
                    "evaluator": evaluator["version"],
                    "new_provider_requests": 0,
                }
            )
        )
    finally:
        engine.dispose()
        redis.close()


if __name__ == "__main__":
    main()
