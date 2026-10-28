"""Append a T09 source-matched version after independent real-model checks."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from redis import Redis

from app.agents.versions import register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.executors.service import RunService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    directory = args.evidence.resolve()
    assert directory.is_relative_to((PROJECT_ROOT / "docs/evidence/T09").resolve())
    report = json.loads((directory / "matrix.json").read_bytes())
    assert len(report["scenarios"]) == 9 and report["UI_returncode"] == 0
    settings = Settings()
    assert not settings.allow_paid_model_calls and not settings.executor_allow_paid_model_calls
    redis = Redis.from_url(settings.redis_url)
    previous = json.loads((directory.parent / "preparation.json").read_bytes())["old_ledgers"]
    assert {
        k: {f.decode(): int(v) for f, v in redis.hgetall(k).items()} for k in previous
    } == previous
    engine = create_database_engine(settings)
    try:
        manifest = RunService(engine, settings).current_manifest()
        for kind, observed in report["scenarios"].items():
            assert observed["summary"]["verdict"] == "passed"
            assert all(observed["independent_checks"].values())
            snapshot = json.loads((directory / f"{kind}-run.json").read_bytes())
            target = snapshot["manifest"]["target"]
            assert target["sources"] == manifest["sources"]
            assert target["context"] == manifest["context"]
            if not observed["expected"].get("knowledge"):
                assert target["config_hash"] == manifest["config_hash"]
            assert snapshot["executions"][0]["timing"]["gateway_mode"] == "real_provider"
        version = register_version(engine, manifest)
        output = {
            "checked_at": datetime.now(UTC).isoformat(),
            "version_id": version,
            "manifest": manifest,
            "evidence": directory.relative_to(PROJECT_ROOT).as_posix(),
            "unchanged_old_ledgers": previous,
            "team_acceptance": "pending",
            "boundary": (
                "Nine real-provider development runs; custom isolated KB variants recorded "
                "separately. No new Linux dual-worker experiment."
            ),
        }
        (directory.parent / "frozen-v0.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"version_id": version, "config_hash": manifest["config_hash"]}))
    finally:
        engine.dispose()
        redis.close()


if __name__ == "__main__":
    main()
