"""Append a source-matched T10 V0 revision after real, human-reviewed comparisons."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from redis import Redis

from app.agents.versions import register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.executors.service import RunService
from app.metamorphic.engine import comparator_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    directory = args.evidence.resolve()
    assert directory.is_relative_to((PROJECT_ROOT / "docs/evidence/T10").resolve())
    report = json.loads((directory / "matrix.json").read_bytes())
    relations = json.loads((directory / "relations.json").read_bytes())
    snapshot = json.loads((directory / "run.json").read_bytes())
    assert report["mode"] == "real_DeepSeek_SQL_Redis_in_process_executor"
    assert report["UI_returncode"] == 0
    assert relations["summary"]["counts"] == {
        "passed": 12,
        "failed": 0,
        "inconclusive": 0,
        "error": 0,
    }
    assert len(snapshot["executions"]) == 13
    assert all(e["timing"]["gateway_mode"] == "real_provider" for e in snapshot["executions"])
    assert relations["catalog"]["review"]["decision"] == "confirmed"
    assert relations["comparator"] == comparator_manifest()
    assert snapshot["manifest"]["relation_comparator"] == comparator_manifest()
    settings = Settings()
    assert not settings.allow_paid_model_calls and not settings.executor_allow_paid_model_calls
    redis = Redis.from_url(settings.redis_url)
    prior = json.loads((directory.parent / "preparation.json").read_bytes())["old_ledgers"]
    assert {k: {f.decode(): int(v) for f, v in redis.hgetall(k).items()} for k in prior} == prior
    engine = create_database_engine(settings)
    try:
        manifest = RunService(engine, settings).current_manifest()
        assert manifest["config_hash"] == snapshot["manifest"]["target"]["config_hash"]
        version = register_version(engine, manifest)
        output = {
            "checked_at": datetime.now(UTC).isoformat(),
            "version_id": version,
            "manifest": manifest,
            "relation_comparator": comparator_manifest(),
            "evidence": directory.relative_to(PROJECT_ROOT).as_posix(),
            "team_acceptance": "pending",
            "boundary": (
                "12 related development relations, one human-reviewed family; "
                "no holdout score or new dual-Linux-worker experiment."
            ),
        }
        destination = directory.parent / "frozen-v0.json"
        assert not destination.exists(), "Preserve the first freeze record"
        destination.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"version_id": version, "config_hash": manifest["config_hash"]}))
    finally:
        engine.dispose()
        redis.close()


if __name__ == "__main__":
    main()
