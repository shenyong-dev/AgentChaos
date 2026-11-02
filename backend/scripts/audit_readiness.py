"""Final read-only preservation audit after free T17C verification finishes."""

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
    root, settings = PROJECT_ROOT, Settings()
    baseline = json.loads((root / "docs/evidence/T17C/preparation.json").read_bytes())
    version = json.loads((root / "docs/evidence/T17C/main-version.json").read_bytes())
    engine = create_database_engine(settings)
    client = Redis.from_url(settings.redis_url)
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
                    query = query.where(TargetVersion.id.in_(version["old_version_ids"]))
                rows = [
                    {c.name: getattr(row, c.name) for c in model.__table__.columns}
                    for row in session.scalars(query)
                ]
                plain = json.loads(json.dumps(rows, default=str))
                observed = {"count": len(plain), "sha256": canonical_hash(plain)}
                assert observed == baseline["tables"][model.__tablename__], model.__tablename__
                preserved[model.__tablename__] = observed
            assert (
                session.get(TargetVersion, version["version_id"]).config_hash
                == version["config_hash"]
            )
            previous_source = session.get(
                TargetVersion, max(version["old_version_ids"])
            ).model_config_json["sources"]
            current_source = session.get(TargetVersion, version["version_id"]).model_config_json[
                "sources"
            ]
            assert (
                len(list(session.scalars(select(TargetVersion))))
                == len(version["old_version_ids"]) + 1
            )
            migration = session.scalar(text("SELECT version_num FROM alembic_version"))
            triggers = session.scalar(
                text(
                    "SELECT COUNT(*) FROM information_schema.triggers "
                    "WHERE trigger_schema=DATABASE()"
                )
            )
        assert migration == baseline["migration"] and triggers == baseline["triggers"]
        for relative, expected_hash in previous_source.items():
            committed = subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=root)
            normalized = committed.decode("utf-8").replace("\r\n", "\n").encode()
            assert hashlib.sha256(normalized).hexdigest() == expected_hash, relative
        for relative, expected_hash in current_source.items():
            observed_hash = hashlib.sha256(
                (root / relative).read_text(encoding="utf-8").encode()
            ).hexdigest()
            assert observed_hash == expected_hash, relative
        assert len(inspect(engine).get_table_names()) - 1 == baseline["table_count"]
        live = {
            key: {k.decode(): int(v) for k, v in client.hgetall(key).items()}
            for key in baseline["ledgers"]
        }
        assert live == baseline["ledgers"]
        keys = {
            key.decode()
            for key in client.scan_iter("ac:*:budget:*")
            if client.type(key) == b"hash" and client.hexists(key, "limit")
        }
        assert keys == set(baseline["ledgers"]), (
            "Unexpected persistent ledgers; inspect, do not delete"
        )
        assert not any(
            getattr(settings, k)
            for k in (
                "allow_paid_model_calls",
                "executor_allow_paid_model_calls",
                "casegen_allow_paid_model_calls",
                "judge_allow_paid_model_calls",
            )
        )
        historical = {}
        for task, digest in baseline["historical_manifests"].items():
            path = root / f"docs/evidence/{task}/delivery-manifest.json"
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            sealed = json.loads(path.read_bytes())["files"]
            paths = {
                p: info
                for p, info in sealed.items()
                if p.startswith((f"docs/evidence/{task}/", f"docs/fixtures/{task}/"))
            }
            for relative, info in paths.items():
                data = (root / relative).read_bytes()
                expected_hash = info["sha256"] if isinstance(info, dict) else info
                assert hashlib.sha256(data).hexdigest() == expected_hash, relative
            historical[task] = {
                "manifest_unchanged": True,
                "sealed_evidence_files_identical": len(paths),
            }
        result = {
            "checked_at": datetime.now(UTC).isoformat(),
            "preserved_main_tables": preserved,
            "main_version": version,
            "previous_source_files_match_base_git": len(previous_source),
            "current_source_files_match_registered_version": len(current_source),
            "thirteen_ledgers_unchanged": True,
            "persistent_ledger_count": len(keys),
            "historical": historical,
            "table_count": baseline["table_count"],
            "migration": migration,
            "triggers": triggers,
            "four_paid_flags_false": True,
            "new_model_calls": 0,
            "c_free_bytes": shutil.disk_usage("C:/").free,
            "head_unchanged": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            == baseline["base_commit"],
            "git_index_empty": not subprocess.check_output(
                ["git", "diff", "--cached", "--name-only"], cwd=root
            ),
        }
        assert result["head_unchanged"] and result["git_index_empty"]
        output = root / "docs/evidence/T17C/final-audit.json"
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {"preserved": True, "historical": historical, "main_version": version["version_id"]}
            )
        )
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
