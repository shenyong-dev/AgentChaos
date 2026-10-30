"""Read-only T17 baseline. Hash historical records instead of copying them again."""

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
    output = root / "docs/evidence/T17/preparation.json"
    if output.exists():
        raise SystemExit("Existing T17 baseline must not be overwritten")
    settings = Settings()
    engine = create_database_engine(settings)
    redis = Redis.from_url(settings.redis_url, socket_timeout=10)
    try:
        old = json.loads((root / "docs/evidence/T16/preparation.json").read_bytes())["ledgers"]
        final = json.loads((root / "docs/evidence/T16/audits/20261029T155905.json").read_bytes())[
            "role_ledgers"
        ]
        for role, auth in (
            ("agent", "T16-20261029-agent-54.30"),
            ("judge", "T16-20261029-judge-1.35"),
        ):
            old[f"ac:t16:budget:{auth}:{role}"] = final[role]
        ledgers = {k: {f.decode(): int(v) for f, v in redis.hgetall(k).items()} for k in old}
        assert ledgers == old
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
                values = [
                    {column.name: getattr(row, column.name) for column in model.__table__.columns}
                    for row in session.scalars(select(model).order_by(model.id))
                ]
                # Timestamp/Decimal conversion matches the final comparison script.
                plain = json.loads(json.dumps(values, default=str))
                tables[model.__tablename__] = {"count": len(plain), "sha256": canonical_hash(plain)}
            migration = session.scalar(text("SELECT version_num FROM alembic_version"))
            triggers = session.scalar(
                text("SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema=:db"),
                {"db": settings.mysql_database},
            )
        raw = subprocess.check_output(["git", "ls-tree", "-rz", "HEAD"], cwd=root)
        evidence = [
            row for row in raw.split(b"\0") if b"\tdocs/evidence/" in row or b"\tdatasets/" in row
        ]
        record = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "authorized_task": "T17 only; user requested next task after T16 delivery",
            "base_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
            "main_tables": tables,
            "table_count": len(inspect(engine).get_table_names()) - 1,
            "triggers": triggers,
            "migration": migration,
            "old_ten_ledgers": ledgers,
            "historical_index_files": len(evidence),
            "historical_index_tree_sha256": hashlib.sha256(b"\0".join(evidence)).hexdigest(),
            "T16_manifest_sha256": hashlib.sha256(
                (root / "docs/evidence/T16/delivery-manifest.json").read_bytes()
            ).hexdigest(),
            "c_free_bytes": shutil.disk_usage("C:/").free,
            "new_model_calls": 0,
            "new_budget_authorized": False,
            "observed_initial_ports": {"mysql": True, "redis": False, "api": False, "ui": False},
            "recovery": "Existing D: scripts restored Redis and existing Docker engine; no install",
            "evidence_policy": "One compact summary/raw archive per lab; no duplicate videos",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {k: v for k, v in record.items() if k not in ("old_ten_ledgers", "main_tables")}
            )
        )
    finally:
        engine.dispose()
        redis.close()


if __name__ == "__main__":
    main()
