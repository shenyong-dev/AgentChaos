"""Read-only delivery audit of historical bytes, main SQL and all role ledgers."""

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
from reliability.real import AUTH_KEY


def sha(body):
    return hashlib.sha256(body).hexdigest()


def main():
    root = PROJECT_ROOT
    baseline = json.loads((root / "docs/evidence/T17/preparation.json").read_bytes())
    s = Settings()
    assert not any(
        (
            s.allow_paid_model_calls,
            s.executor_allow_paid_model_calls,
            s.casegen_allow_paid_model_calls,
            s.judge_allow_paid_model_calls,
        )
    )
    client = Redis.from_url(s.redis_url)
    old = {
        k: {f.decode(): int(v) for f, v in client.hgetall(k).items()}
        for k in baseline["old_ten_ledgers"]
    }
    assert old == baseline["old_ten_ledgers"]
    live = {k.decode(): int(v) for k, v in client.hgetall(AUTH_KEY).items()}
    assert live["limit"] == 1_000_000 and live["calls_started"] <= 60
    assert live["charged_estimate"] + live["held_upper"] <= 1_000_000
    raw = subprocess.check_output(["git", "ls-tree", "-rz", "HEAD"], cwd=root)
    evidence = [r for r in raw.split(b"\0") if b"\tdocs/evidence/" in r or b"\tdatasets/" in r]
    assert sha(b"\0".join(evidence)) == baseline["historical_index_tree_sha256"]
    normalized = []
    for row in evidence:
        header, path = row.split(b"\t", 1)
        oid = header.split()[2].decode()
        body = (root / path.decode()).read_bytes()

        def blob(value):
            return hashlib.sha1(b"blob " + str(len(value)).encode() + b"\0" + value).hexdigest()

        if blob(body) != oid:
            assert b"\r\n" in body and blob(body.replace(b"\r\n", b"\n")) == oid, path
            normalized.append(path.decode())
    assert len(normalized) == 789
    engine = create_database_engine(s)
    main_tables = {}
    with Session(engine) as session:
        for model in (TargetVersion, TestCase, Artifact, Booking, Defect, TestRun, TestExecution):
            values = [
                {c.name: getattr(row, c.name) for c in model.__table__.columns}
                for row in session.scalars(select(model).order_by(model.id))
            ]
            plain = json.loads(json.dumps(values, default=str))
            key = model.__tablename__
            prior = baseline["main_tables"][key]
            assert canonical_hash(plain[: prior["count"]]) == prior["sha256"], key
            if model is not TargetVersion:
                assert len(plain) == prior["count"], key
            main_tables[key] = {
                "count": len(plain),
                "original_count": prior["count"],
                "original_rows_unchanged": True,
            }
        triggers = session.scalar(
            text("SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema=:db"),
            {"db": s.mysql_database},
        )
        migration = session.scalar(text("SELECT version_num FROM alembic_version"))
    assert triggers == 23 and migration == "20261028_0006"
    assert len(inspect(engine).get_table_names()) - 1 == 16
    archives = []
    for manifest in sorted((root / "docs/evidence/T17").glob("*/raw-evidence-manifest.json")):
        meta = json.loads(manifest.read_bytes())
        source = manifest.with_name("raw-evidence.zip")
        assert sha(source.read_bytes()) == meta["archive_sha256"]
        with zipfile.ZipFile(source) as archive:
            assert set(archive.namelist()) == set(meta["entries"])
            for name, expected in meta["entries"].items():
                body = archive.read(name)
                assert sha(body) == expected["sha256"] and len(body) == expected["bytes"]
        archives.append(
            {
                "directory": manifest.parent.name,
                "entries": len(meta["entries"]),
                "bytes": source.stat().st_size,
                "failed": meta["failed"],
            }
        )
    provider_files = 0
    for source in (root / "docs/evidence/T17").glob("real-*/provider-calls.zip"):
        calls = json.loads(source.with_name("live-calls.json").read_bytes())["calls"]
        with zipfile.ZipFile(source) as archive:
            names = [n for n in archive.namelist() if n.startswith("real-calls/")]
            assert len(names) == len(calls)
            for call in calls:
                name = next(n for n in names if n.endswith("/" + call["call_id"] + ".json"))
                assert sha(archive.read(name)) == call["sha256"]
            provider_files += len(names)
    assert provider_files == live["calls_started"]
    secret = s.deepseek_api_key.get_secret_value().encode()
    assert len(secret) > 12
    for path in (root / "docs/evidence/T17").rglob("*"):
        if path.is_file():
            if path.suffix == ".zip":
                with zipfile.ZipFile(path) as archive:
                    assert all(secret not in archive.read(n) for n in archive.namelist())
            else:
                assert secret not in path.read_bytes(), path
    assert (
        sha((root / "docs/evidence/T16/delivery-manifest.json").read_bytes())
        == baseline["T16_manifest_sha256"]
    )
    contracts = {}
    # Capture generated contracts in memory; never overwrite an old artifact.
    import runpy
    from pathlib import Path
    from unittest.mock import patch

    def capture(path, body, **kwargs):
        contracts[path.name] = body.encode("utf-8").replace(b"\n", b"\r\n")
        return len(body)

    with patch.object(Path, "write_text", capture):
        runpy.run_path(str(root / "backend/scripts/export_contracts.py"))
    for name, body in contracts.items():
        assert body.replace(b"\r\n", b"\n") == (
            root / "docs/architecture" / name
        ).read_bytes().replace(b"\r\n", b"\n"), name
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "main_tables": main_tables,
        "schema": {"tables": 16, "triggers": 23, "migration": migration},
        "old_ten_ledgers_unchanged": True,
        "T17_ledger": live,
        "historical_evidence_files": len(evidence),
        "preexisting_CRLF_files_retained": len(normalized),
        "T16_manifest_unchanged": sha(
            (root / "docs/evidence/T16/delivery-manifest.json").read_bytes()
        )
        == baseline["T16_manifest_sha256"],
        "archives": archives,
        "provider_calls_byte_verified": provider_files,
        "new_evidence_model_key_scan": "passed, including compressed members",
        "contracts_identical": len(contracts),
        "c_free_bytes": shutil.disk_usage("C:/").free,
        "daily_paid_switches": False,
        "new_evidence_bytes": sum(
            p.stat().st_size for p in (root / "docs/evidence/T17").rglob("*") if p.is_file()
        ),
    }
    output = root / "docs/evidence/T17/final-audit.json"
    assert not output.exists()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
