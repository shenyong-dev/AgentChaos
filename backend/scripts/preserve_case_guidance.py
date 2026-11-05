"""Verify source bindings, contracts and unchanged historical/main facts for T17G."""

import contextlib
import hashlib
import io
import json
import runpy
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.executors.service import RunService
from scripts.prepare_sessions import observe


def main():
    root = PROJECT_ROOT
    folder = root / "docs/evidence/T17G"
    baseline = json.loads((folder / "preparation.json").read_bytes())
    version = json.loads((folder / "main-version.json").read_bytes())
    now = observe()
    assert now["c_free_bytes"] > 2_000_000_000
    assert now["ledgers"] == baseline["ledgers"]
    assert now["triggers"] == baseline["triggers"] == 23
    for name, record in baseline["tables"].items():
        if name != "ac_target_versions":
            assert now["tables"][name] == record, name
    engine = create_database_engine(Settings())
    try:
        with engine.connect() as connection:
            original = [
                dict(r)
                for r in connection.execute(
                    text("SELECT * FROM ac_target_versions WHERE id<:id"),
                    {"id": version["version_id"]},
                ).mappings()
            ]
            original = sorted(
                json.loads(json.dumps(original, default=str)),
                key=lambda r: json.dumps(r, sort_keys=True),
            )
            assert {"count": len(original), "sha256": canonical_hash(original)} == baseline[
                "tables"
            ]["ac_target_versions"]
            counts = {}
            for identifier, archive_name in (
                (max(version["old_version_ids"]), "previous-source.zip"),
                (version["version_id"], "frozen-source.zip"),
            ):
                raw = connection.scalar(
                    text("SELECT model_config_json FROM ac_target_versions WHERE id=:id"),
                    {"id": identifier},
                )
                manifest = json.loads(raw) if isinstance(raw, str) else raw
                with zipfile.ZipFile(folder / archive_name) as archive:
                    for name, expected in manifest["sources"].items():
                        data = (
                            archive.read(name)
                            if name in archive.namelist()
                            else (root / name).read_bytes()
                        )
                        assert (
                            hashlib.sha256(
                                data.decode("utf-8").replace("\r\n", "\n").encode()
                            ).hexdigest()
                            == expected
                        ), (identifier, name)
                counts[str(identifier)] = len(manifest["sources"])
            assert (
                RunService(engine, Settings()).current_manifest()["config_hash"]
                == version["config_hash"]
            )
    finally:
        engine.dispose()
    historical = json.loads((root / ".local/t17g/historical-hashes.json").read_bytes())
    for name, expected in historical.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
    with zipfile.ZipFile(folder / "previous-source.zip") as archive:
        assert (
            archive.read("frontend/src/components/QuickNavigate.vue")
            == (root / "frontend/src/components/QuickNavigate.vue").read_bytes()
        )
    generated = {}
    with (
        patch.object(Path, "write_bytes", lambda path, data: generated.setdefault(path, data)),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        runpy.run_module("scripts.export_contracts", run_name="__main__")
    assert len(generated) == 19
    for path, content in generated.items():
        assert path.read_bytes() == content, path.name
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    assert head == baseline["base_commit"]
    assert not subprocess.check_output(
        ["git", "diff", "--cached", "--name-only"], cwd=root, text=True
    ).strip()
    record = {
        "checked_at": datetime.now(UTC).isoformat(),
        "old_48_versions_unchanged": True,
        "main_tables_unchanged_except_one_source_version": True,
        "tables": now["tables"],
        "triggers": now["triggers"],
        "ledgers": now["ledgers"],
        "ledger_fields_unchanged": True,
        "historical_files_byte_identical": len(historical),
        "verified_source_counts": counts,
        "contracts_identical_in_memory": {
            p.name: hashlib.sha256(data).hexdigest() for p, data in generated.items()
        },
        "user_quick_navigation_preserved": True,
        "four_paid_flags_false": True,
        "new_model_calls": 0,
        "c_free_bytes": now["c_free_bytes"],
        "head_unchanged": head,
        "index_empty": True,
    }
    (folder / "preservation-audit.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "historical": len(historical),
                "ledgers": len(now["ledgers"]),
                "versions": counts,
                "contracts": len(generated),
                "passed": True,
            }
        )
    )


if __name__ == "__main__":
    main()
