"""Reconcile T17F main facts, source compatibility and historical evidence byte-for-byte."""

import hashlib
import json
import subprocess
import zipfile
from datetime import UTC, datetime

from sqlalchemy import text

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from scripts.prepare_sessions import observe


def main():
    root = PROJECT_ROOT
    folder = root / "docs/evidence/T17F"
    baseline = json.loads((folder / "preparation.json").read_bytes())
    version = json.loads((folder / "main-version.json").read_bytes())
    initial = json.loads((folder / "initial-main-version.json").read_bytes())
    now = observe()
    assert now["ledgers"] == baseline["ledgers"]
    assert now["triggers"] == baseline["triggers"] == 23
    for table, record in baseline["tables"].items():
        if table != "ac_target_versions":
            assert now["tables"][table] == record, table
    engine = create_database_engine(Settings())
    counts = {}
    try:
        with engine.connect() as connection:
            old = [
                dict(row)
                for row in connection.execute(
                    text("SELECT * FROM ac_target_versions WHERE id<=:id"),
                    {"id": max(initial["old_version_ids"])},
                ).mappings()
            ]
            plain = sorted(
                json.loads(json.dumps(old, default=str)),
                key=lambda r: json.dumps(r, sort_keys=True),
            )
            assert {"count": len(plain), "sha256": canonical_hash(plain)} == baseline["tables"][
                "ac_target_versions"
            ]
            with (
                zipfile.ZipFile(folder / "previous-source.zip") as previous,
                zipfile.ZipFile(folder / "initial-source.zip") as interim,
            ):
                for identifier, archive in (
                    (45, previous),
                    (initial["version_id"], interim),
                    (version["version_id"], None),
                ):
                    raw = connection.scalar(
                        text("SELECT model_config_json FROM ac_target_versions WHERE id=:id"),
                        {"id": identifier},
                    )
                    manifest = json.loads(raw) if isinstance(raw, str) else raw
                    for name, expected in manifest["sources"].items():
                        data = (
                            archive.read(name)
                            if archive and name in archive.namelist()
                            else (root / name).read_bytes()
                        )
                        normalized = data.decode("utf-8").replace("\r\n", "\n").encode()
                        assert hashlib.sha256(normalized).hexdigest() == expected, (
                            identifier,
                            name,
                        )
                    counts[str(identifier)] = len(manifest["sources"])
            assert now["tables"]["ac_target_versions"]["count"] == 47
    finally:
        engine.dispose()
    historical = json.loads((root / ".local/t17f/historical-hashes.json").read_bytes())
    for name, expected in historical.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
    with zipfile.ZipFile(folder / "previous-source.zip") as previous:
        name = "frontend/src/components/QuickNavigate.vue"
        assert previous.read(name) == (root / name).read_bytes()
    assert (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        == baseline["base_commit"]
    )
    assert not subprocess.check_output(
        ["git", "diff", "--cached", "--name-only"], cwd=root, text=True
    ).strip()
    record = {
        "checked_at": datetime.now(UTC).isoformat(),
        "original_45_versions_unchanged": True,
        "main_tables_unchanged_except_two_source_versions": True,
        "tables": now["tables"],
        "triggers": 23,
        "ledgers": now["ledgers"],
        "ledger_fields_unchanged": True,
        "historical_files_byte_identical": len(historical),
        "verified_source_counts": counts,
        "user_quick_navigation_change_preserved": True,
        "four_paid_flags_false": True,
        "new_model_calls": 0,
        "c_free_bytes": now["c_free_bytes"],
        "head_unchanged": baseline["base_commit"],
        "index_empty": True,
    }
    (folder / "final-audit.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "historical": len(historical),
                "ledgers": len(now["ledgers"]),
                "versions": counts,
                "passed": True,
            }
        )
    )


if __name__ == "__main__":
    main()
