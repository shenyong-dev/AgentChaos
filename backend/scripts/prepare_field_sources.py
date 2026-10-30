"""Read-only T17A baseline; keep historical evidence and model ledgers intact."""

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime

from redis import Redis
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.models import Artifact, Booking, Defect, TargetVersion, TestCase, TestExecution, TestRun


def main():
    root = PROJECT_ROOT
    output = root / "docs/evidence/T17A/preparation.json"
    assert not output.exists(), "Do not overwrite the original baseline"
    settings = Settings()
    assert not any(
        getattr(settings, name)
        for name in (
            "allow_paid_model_calls",
            "executor_allow_paid_model_calls",
            "casegen_allow_paid_model_calls",
            "judge_allow_paid_model_calls",
        )
    )
    assert shutil.disk_usage("C:/").free > 2_000_000_000
    engine = create_database_engine(settings)
    redis = Redis.from_url(settings.redis_url, socket_timeout=10)
    try:
        tables = {}
        with Session(engine) as session:
            for model in (
                TargetVersion,
                TestCase,
                Artifact,
                Booking,
                Defect,
                TestRun,
                TestExecution,
            ):
                rows = [
                    {column.name: getattr(row, column.name) for column in model.__table__.columns}
                    for row in session.scalars(select(model).order_by(model.id))
                ]
                plain = json.loads(json.dumps(rows, default=str))
                tables[model.__tablename__] = {"count": len(plain), "sha256": canonical_hash(plain)}
            migration = session.scalar(text("SELECT version_num FROM alembic_version"))
            triggers = session.scalar(
                text("SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema=:db"),
                {"db": settings.mysql_database},
            )
        ledger_keys = sorted(
            key.decode()
            for key in redis.scan_iter("ac:*:budget:*")
            if redis.type(key) == b"hash"
            and b":call:" not in key
            and b":run:" not in key
            and redis.hexists(key, "limit")
        )
        ledgers = {
            key: {k.decode(): int(v) for k, v in redis.hgetall(key).items()} for key in ledger_keys
        }
        assert len(ledgers) >= 11, "Expected T04 through T17 authorizations"
        snapshot = root / ".local/t17a/baseline-source"
        snapshot.mkdir(parents=True, exist_ok=False)
        sources = {}
        for relative in (
            "backend/app/agents/state.py",
            "backend/app/agents/runtime.py",
            "backend/app/agents/graph.py",
            "任务规划.md",
        ):
            data = (root / relative).read_bytes()
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            sources[relative] = hashlib.sha256(data).hexdigest()
        historical = {}
        for name in ("T16", "T17"):
            manifest = root / f"docs/evidence/{name}/delivery-manifest.json"
            historical[name] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        record = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "authorized_task": "T17A only; user accepted the next task on 2026-10-30",
            "base_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
            "tables": tables,
            "table_count": len(inspect(engine).get_table_names()) - 1,
            "migration": migration,
            "triggers": triggers,
            "ledgers": ledgers,
            "prior_sources": sources,
            "historical_manifests": historical,
            "c_free_bytes": shutil.disk_usage("C:/").free,
            "paid_flags_false": True,
            "new_provider_calls": 0,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"tables": tables, "old_ledgers": len(ledgers), "new_calls": 0}))
    finally:
        redis.close()
        engine.dispose()


if __name__ == "__main__":
    main()
