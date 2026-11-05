"""T17F baseline: read main facts/ledgers; archive original source without secrets."""

import hashlib
import json
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime

from redis import Redis
from sqlalchemy import inspect, text

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine


def observe():
    settings = Settings()
    flags = [
        getattr(settings, n)
        for n in (
            "allow_paid_model_calls",
            "executor_allow_paid_model_calls",
            "casegen_allow_paid_model_calls",
            "judge_allow_paid_model_calls",
        )
    ]
    assert not any(flags)
    engine = create_database_engine(settings)
    client = Redis.from_url(settings.redis_url, socket_timeout=3, socket_connect_timeout=3)
    try:
        tables = {}
        with engine.connect() as connection:
            for name in sorted(inspect(engine).get_table_names()):
                rows = [
                    dict(row)
                    for row in connection.execute(text(f"SELECT * FROM `{name}`")).mappings()
                ]
                rows = sorted(
                    json.loads(json.dumps(rows, default=str)),
                    key=lambda r: json.dumps(r, sort_keys=True),
                )
                tables[name] = {"count": len(rows), "sha256": canonical_hash(rows)}
            triggers = connection.scalar(
                text(
                    "SELECT COUNT(*) FROM information_schema.triggers "
                    "WHERE trigger_schema=DATABASE()"
                )
            )
        ledgers = {
            k.decode(): {a.decode(): int(b) for a, b in client.hgetall(k).items()}
            for k in sorted(client.scan_iter("ac:*:budget:*"))
            if client.type(k) == b"hash" and client.hexists(k, "limit")
        }
        assert len(ledgers) == 14
        return {
            "tables": tables,
            "ledgers": ledgers,
            "triggers": triggers,
            "four_paid_flags_false": True,
            "c_free_bytes": shutil.disk_usage("C:/").free,
        }
    finally:
        engine.dispose()
        client.close()


def main():
    root = PROJECT_ROOT
    output = root / "docs/evidence/T17F"
    output.mkdir(parents=True, exist_ok=True)
    assert not (output / "preparation.json").exists()
    baseline = observe()
    assert baseline["c_free_bytes"] > 2_000_000_000
    paths = []
    for folder in ("backend/app", "frontend/src", "docs/architecture"):
        paths.extend(
            p for p in (root / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts
        )
    paths += [
        root / p
        for p in (
            "任务规划.md",
            "backend/tests/test_database.py",
            "backend/scripts/export_contracts.py",
        )
    ]
    with zipfile.ZipFile(output / "previous-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            archive.write(path, path.relative_to(root).as_posix())
    historical = {}
    for folder in ("docs/evidence", "docs/fixtures"):
        for path in (root / folder).rglob("*"):
            if path.is_file() and output not in path.parents:
                historical[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    local = root / ".local/t17f"
    local.mkdir(parents=True, exist_ok=True)
    (local / "historical-hashes.json").write_text(json.dumps(historical), encoding="utf-8")
    baseline.update(
        recorded_at=datetime.now(UTC).isoformat(),
        authorized_task="T17F only; user requested next step 2026-11-03",
        base_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        preexisting_changes=["frontend/src/components/QuickNavigate.vue: append-to-body"],
        historical_files=len(historical),
        source_archive="previous-source.zip",
        new_model_calls=0,
    )
    (output / "preparation.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "tables": len(baseline["tables"]),
                "ledgers": len(baseline["ledgers"]),
                "historical": len(historical),
            }
        )
    )


if __name__ == "__main__":
    main()
