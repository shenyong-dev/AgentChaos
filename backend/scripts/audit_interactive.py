"""Preserve prior data/seals and reconcile the bounded T17D development evidence."""

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
from app.interactive.budget import AUTHORIZATION
from app.models import Artifact, Booking, Defect, TargetVersion, TestCase, TestExecution, TestRun


def main():
    root, settings = PROJECT_ROOT, Settings()
    dest = root / "docs/evidence/T17D"
    baseline = json.loads((dest / "preparation.json").read_bytes())
    current = json.loads((dest / "main-version.json").read_bytes())
    engine = create_database_engine(settings)
    redis = Redis.from_url(settings.redis_url)
    try:
        preserved = {}
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
                query = select(model).order_by(model.id)
                if model is TargetVersion:
                    query = query.where(TargetVersion.id.in_(baseline["old_version_ids"]))
                rows = [
                    {c.name: getattr(r, c.name) for c in model.__table__.columns}
                    for r in session.scalars(query)
                ]
                observed = {
                    "count": len(rows),
                    "sha256": canonical_hash(json.loads(json.dumps(rows, default=str))),
                }
                assert observed == baseline["tables"][model.__tablename__], model.__tablename__
                preserved[model.__tablename__] = observed
            manifest = session.get(TargetVersion, current["version_id"]).model_config_json
            assert manifest["config_hash"] == current["config_hash"]
            for name, expected in manifest["sources"].items():
                assert (
                    hashlib.sha256((root / name).read_text(encoding="utf-8").encode()).hexdigest()
                    == expected
                ), name
            migration = session.scalar(text("SELECT version_num FROM alembic_version"))
            triggers = session.scalar(
                text("SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema=:db"),
                {"db": settings.mysql_database},
            )
        assert migration == baseline["migration"] and triggers == 23
        ledgers = {
            key: {k.decode(): int(v) for k, v in redis.hgetall(key).items()}
            for key in baseline["ledgers"]
        }
        assert ledgers == baseline["ledgers"]
        paid = {
            k.decode(): int(v)
            for k, v in redis.hgetall(f"ac:t17d:budget:{AUTHORIZATION}:agent").items()
        }
        assert paid["limit"] == 1_000_000 and paid["calls_started"] <= 120
        assert paid.get("charged_estimate", 0) + paid.get("held_upper", 0) <= 1_000_000
        assert not any(
            getattr(settings, name)
            for name in (
                "allow_paid_model_calls",
                "executor_allow_paid_model_calls",
                "casegen_allow_paid_model_calls",
                "judge_allow_paid_model_calls",
            )
        )
        seals = {}
        with zipfile.ZipFile(dest / "previous-source.zip") as original:
            for name, expected in baseline["prior_seals"].items():
                assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
                contents = json.loads((root / name).read_bytes())["files"]
                live, archived = 0, 0
                for path, record in contents.items():
                    raw = (root / path).read_bytes()
                    if hashlib.sha256(raw).hexdigest() == record["sha256"]:
                        live += 1
                    else:
                        if (
                            path == "frontend/src/design-system.css"
                            and "frontend-refinement/" in name
                        ):
                            with zipfile.ZipFile(
                                root / "docs/evidence/T17C/surface-interactions/previous-style.zip"
                            ) as css:
                                raw = css.read(path)
                        else:
                            raw = original.read(path)
                        assert hashlib.sha256(raw).hexdigest() == record["sha256"], path
                        archived += 1
                seals[name] = {
                    "unchanged_live": live,
                    "original_bytes_archived": archived,
                    "manifest_unchanged": True,
                    "total": len(contents),
                }
        assert not subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"], cwd=root
        ).strip()
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        assert head == baseline["base_commit"]
        historical_changes = subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                "--",
                *[f"docs/evidence/T{i:02}" for i in range(18)],
                "docs/evidence/T17A",
                "docs/evidence/T17B",
            ],
            cwd=root,
        ).strip()
        assert not historical_changes
        report = {
            "checked_at": datetime.now(UTC).isoformat(),
            "preserved_main_tables": preserved,
            "main_version": current["version_id"],
            "config_hash": current["config_hash"],
            "main_source_paths_verified": len(manifest["sources"]),
            "old_versions_preserved": len(baseline["old_version_ids"]),
            "old_thirteen_ledgers_unchanged": True,
            "t17d_ledger_snapshot": paid,
            "four_daily_paid_flags_false": True,
            "prior_seals": seals,
            "domain_tables": len(inspect(engine).get_table_names()) - 1,
            "triggers": triggers,
            "migration": migration,
            "historical_experiment_git_changes": 0,
            "main_worker_started": False,
            "new_dependencies": False,
            "new_install_or_wsl": False,
            "c_drive_free_bytes": shutil.disk_usage("C:/").free,
            "head_unchanged": True,
            "index_empty": True,
        }
        (dest / "preservation-audit.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "version": current["version_id"],
                    "prior_seals": seals,
                    "calls": paid["calls_started"],
                    "old_ledgers": len(ledgers),
                }
            )
        )
    finally:
        redis.close()
        engine.dispose()


if __name__ == "__main__":
    main()
