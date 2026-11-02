"""Read-only baseline and compact original source archive for T17D."""

import hashlib
import json
import shutil
import subprocess
import zipfile
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
    folder = root / "docs/evidence/T17D"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / "preparation.json"
    assert not output.exists(), "Preserve original baseline"
    settings = Settings()
    assert not any(
        getattr(settings, n)
        for n in (
            "allow_paid_model_calls",
            "executor_allow_paid_model_calls",
            "casegen_allow_paid_model_calls",
            "judge_allow_paid_model_calls",
        )
    )
    assert shutil.disk_usage("C:/").free > 2_000_000_000
    engine = create_database_engine(settings)
    client = Redis.from_url(settings.redis_url, socket_timeout=3)
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
                    {c.name: getattr(row, c.name) for c in model.__table__.columns}
                    for row in session.scalars(select(model).order_by(model.id))
                ]
                tables[model.__tablename__] = {
                    "count": len(rows),
                    "sha256": canonical_hash(json.loads(json.dumps(rows, default=str))),
                }
            versions = list(session.scalars(select(TargetVersion).order_by(TargetVersion.id)))
            latest = versions[-1]
            old_manifest = latest.model_config_json
            version_id = latest.id
            version_hash = latest.config_hash
            for name, expected in old_manifest["sources"].items():
                assert (
                    hashlib.sha256((root / name).read_text(encoding="utf-8").encode()).hexdigest()
                    == expected
                )
            migration = session.scalar(text("SELECT version_num FROM alembic_version"))
        ledger_keys = sorted(
            k.decode()
            for k in client.scan_iter("ac:*:budget:*")
            if client.type(k) == b"hash" and client.hexists(k, "limit")
        )
        ledgers = {
            key: {k.decode(): int(v) for k, v in client.hgetall(key).items()} for key in ledger_keys
        }
        assert len(ledgers) == 13
        seals = [
            "docs/evidence/T17C/delivery-manifest.json",
            "docs/evidence/T17C/frontend-refinement/delivery-manifest.json",
            "docs/evidence/T17C/surface-interactions/delivery-manifest.json",
            "docs/evidence/T17C/theme-interactions/delivery-manifest.json",
        ]
        paths = set(old_manifest["sources"])
        for name in seals:
            paths.update(
                p
                for p in json.loads((root / name).read_bytes())["files"]
                if p.endswith((".py", ".ts", ".vue", ".css", ".html", ".md", ".json"))
            )
        paths.update(
            (
                "frontend/src/main.ts",
                "frontend/src/navigation.ts",
                "frontend/src/components/EnvironmentReadiness.vue",
                "backend/scripts/export_contracts.py",
            )
        )
        with zipfile.ZipFile(folder / "previous-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(paths):
                archive.writestr(name, (root / name).read_bytes())
        data = {
            "checked_at": datetime.now(UTC).isoformat(),
            "tables": tables,
            "old_version_ids": [v.id for v in versions],
            "version_id": version_id,
            "config_hash": version_hash,
            "ledgers": ledgers,
            "migration": migration,
            "domain_tables": len(inspect(engine).get_table_names()) - 1,
            "prior_seals": {
                name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in seals
            },
            "source_archive": "previous-source.zip",
            "archived_paths": len(paths),
            "four_paid_flags_false": True,
            "new_model_calls": 0,
            "base_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
            "c_drive_free_bytes": shutil.disk_usage("C:/").free,
        }
        output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"version": version_id, "ledgers": len(ledgers), "archived": len(paths)}))
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
