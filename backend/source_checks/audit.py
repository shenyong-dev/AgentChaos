"""Final read-only T17A accounting, archives, contracts and historical integrity."""

import hashlib
import json
import runpy
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from redis import Redis
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.agents.budget import peak_estimate_microusd
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.models import Artifact, Booking, Defect, TargetVersion, TestCase, TestExecution, TestRun
from source_checks.verify import KEY


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    root = PROJECT_ROOT
    output = root / "docs/evidence/T17A"
    destination = output / "final-audit.json"
    assert not destination.exists()
    baseline = json.loads((output / "preparation.json").read_bytes())
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
    client = Redis.from_url(settings.redis_url)
    engine = create_database_engine(settings)
    try:

        def ledger(key):
            return {k.decode(): int(v) for k, v in client.hgetall(key).items()}

        assert {key: ledger(key) for key in baseline["ledgers"]} == baseline["ledgers"]
        current_ledger = ledger(KEY)
        assert current_ledger["limit"] == 1_000_000
        assert current_ledger["calls_started"] <= 80
        historical = {}
        for task in ("T16", "T17"):
            path = root / f"docs/evidence/{task}/delivery-manifest.json"
            assert sha(path.read_bytes()) == baseline["historical_manifests"][task]
            manifest = json.loads(path.read_bytes())
            entries = manifest["files"]
            checked = 0
            for name, meta in entries.items():
                if name.startswith(
                    ("docs/evidence/", "docs/fixtures/", "datasets/", "docs/acceptance/")
                ):
                    expected_sha = meta["sha256"] if isinstance(meta, dict) else meta
                    assert sha((root / name).read_bytes()) == expected_sha, name
                    checked += 1
            historical[task] = {"manifest_unchanged": True, "historical_files_checked": checked}
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
                query = select(model).order_by(model.id)
                if model is TargetVersion:
                    query = query.where(model.id <= 41)
                rows = [
                    {c.name: getattr(row, c.name) for c in row.__table__.columns}
                    for row in session.scalars(query)
                ]
                plain = json.loads(json.dumps(rows, default=str))
                tables[model.__tablename__] = {"count": len(plain), "sha256": canonical_hash(plain)}
            assert tables == baseline["tables"]
            migration = session.scalar(text("SELECT version_num FROM alembic_version"))
            triggers = session.scalar(
                text("SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema=:db"),
                {"db": settings.mysql_database},
            )
            new_version = session.get(TargetVersion, 42)
            new_hash = new_version.config_hash
        assert migration == baseline["migration"] and triggers == baseline["triggers"]
        assert len(inspect(engine).get_table_names()) - 1 == baseline["table_count"]
        calls, tokens, estimate, cohorts = [], 0, 0, []
        sql_tickets, sql_estimate, business_checks = [], 0, []
        for directory in sorted(output.glob("real-*")):
            if not directory.is_dir():
                continue
            summary = json.loads((directory / "summary.json").read_bytes())
            expected = json.loads((directory / "archive-manifest.json").read_bytes())
            count = 0
            with zipfile.ZipFile(directory / "raw-evidence.zip") as archive:
                assert set(archive.namelist()) == set(expected)
                for name, meta in expected.items():
                    body = archive.read(name)
                    assert sha(body) == meta["sha256"] and len(body) == meta["bytes"]
                    if "/provider/" in name:
                        call = json.loads(body)
                        assert call["call_id"] not in calls
                        calls.append(call["call_id"])
                        usage = call["usage"]
                        assert usage["input_tokens"] >= 0 and usage["output_tokens"] >= 0
                        tokens += usage["input_tokens"] + usage["output_tokens"]
                        estimate += peak_estimate_microusd(
                            usage["input_tokens"], usage["output_tokens"]
                        )
                        count += 1
                    if name.endswith("/run.json"):
                        snapshot = json.loads(body)
                        for execution in snapshot["executions"]:
                            usage = execution["usage"]
                            assert usage["roles"]["judge"]["calls_started"] == 0
                            assert usage["roles"]["case_generator"]["calls_started"] == 0
                            for ticket in usage["tickets"].values():
                                assert ticket["state"] == "settled"
                                sql_tickets.append(ticket["call_id"])
                                sql_estimate += ticket["estimate_microusd"]
                    if name.endswith("/sql.json"):
                        sql = json.loads(body)
                        prefix = name.split("/")[0]
                        snapshot = json.loads(archive.read(prefix + "/run.json"))
                        after = snapshot["executions"][0]["after"]
                        assert {row["booking_uuid"] for row in sql["bookings"]} == {
                            row["booking_id"] for row in after
                        }
                        for row in after:
                            assert row["room_code"] == "A101" and row["attendees"] == 12
                            assert row["start_at"] == "2026-10-31T06:00:00+00:00"
                            assert row["end_at"] == "2026-10-31T07:00:00+00:00"
                        business_checks.append(
                            {
                                "cohort": directory.name,
                                "case": prefix,
                                "actual_bookings": len(after),
                                "present_rows_match_room_date_time_people": True,
                            }
                        )
            if (directory / "source-candidate.zip").exists():
                manifest = json.loads((directory / "version.json").read_bytes())
                with zipfile.ZipFile(directory / "source-candidate.zip") as archive:
                    assert set(archive.namelist()) == set(manifest["sources"])
                    for name, expected_sha in manifest["sources"].items():
                        assert sha(archive.read(name)) == expected_sha
            cohorts.append(
                {
                    "directory": directory.name,
                    "requests": count,
                    "scenarios": len(summary["scenarios"]),
                    "matching_checks": sum(
                        r["matches_declared_business_checks"] for r in summary["scenarios"]
                    ),
                }
            )
        assert len(calls) == current_ledger["calls_started"] == current_ledger["calls_completed"]
        assert sorted(sql_tickets) == sorted(calls) and sql_estimate == estimate
        assert tokens == current_ledger["token_upper"]
        assert estimate == current_ledger["charged_estimate"] and current_ledger["held_upper"] == 0
        secret = settings.deepseek_api_key.get_secret_value().encode()
        assert len(secret) > 12
        for path in output.rglob("*"):
            if path.is_file():
                if path.suffix == ".zip":
                    with zipfile.ZipFile(path) as archive:
                        assert all(secret not in archive.read(name) for name in archive.namelist())
                else:
                    assert secret not in path.read_bytes(), path
        # Normalized text agreement preserves known historical LF/CRLF differences.
        contracts = {}

        def capture(path, body, **kwargs):
            contracts[path.name] = body.encode()
            return len(body)

        with patch.object(Path, "write_text", capture):
            runpy.run_path(str(root / "backend/scripts/export_contracts.py"))
        for name, body in contracts.items():
            assert body.replace(b"\r\n", b"\n") == (
                root / "docs/architecture" / name
            ).read_bytes().replace(b"\r\n", b"\n")
        assert not subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=root)
        result = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "old_evidence": historical,
            "old_eleven_ledgers_unchanged": True,
            "ledger": current_ledger,
            "provider_requests_verified": len(calls),
            "known_total_tokens": tokens,
            "peak_estimate_microusd": estimate,
            "not_a_bill": True,
            "cohorts": cohorts,
            "sql_ticket_mirrors_match_provider_and_global_ledger": True,
            "business_crosschecks": business_checks,
            "main_old_records_unchanged": tables,
            "new_version_id": 42,
            "new_config_hash": new_hash,
            "schema_tables": baseline["table_count"],
            "triggers": triggers,
            "migration": migration,
            "contracts_identical": len(contracts),
            "public_evidence_secret_scan": "passed",
            "daily_paid_flags_false": True,
            "index_empty": True,
            "c_free_bytes": shutil.disk_usage("C:/").free,
            "evidence_bytes_before_audit": sum(
                p.stat().st_size for p in output.rglob("*") if p.is_file()
            ),
        }
        destination.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "verified_calls": len(calls),
                    "tokens": tokens,
                    "microusd": estimate,
                    "old_ledgers": len(baseline["ledgers"]),
                    "contracts": len(contracts),
                }
            )
        )
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
