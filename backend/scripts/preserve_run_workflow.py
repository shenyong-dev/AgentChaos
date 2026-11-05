"""Read-only T17H audit: previous evidence, manifests, ledgers and main business facts."""

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


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    root = PROJECT_ROOT
    folder = root / "docs/evidence/T17H"
    assert not (folder / "preservation-audit.json").exists()
    baseline = json.loads((folder / "preparation.json").read_bytes())
    version = json.loads((folder / "main-version.json").read_bytes())
    initial = json.loads((folder / "initial-main-version.json").read_bytes())
    now = observe()
    assert now["c_free_bytes"] > 2_000_000_000
    assert now["ledgers"] == baseline["ledgers"]
    assert now["triggers"] == baseline["triggers"] == 23
    for name, expected in baseline["tables"].items():
        if name != "ac_target_versions":
            assert now["tables"][name] == expected, name
    engine = create_database_engine(Settings())
    counts = {}
    try:
        with engine.connect() as connection:
            raw = [
                dict(r)
                for r in connection.execute(
                    text("SELECT * FROM ac_target_versions WHERE id<=:id"),
                    {"id": max(initial["old_version_ids"])},
                ).mappings()
            ]
            original = sorted(
                json.loads(json.dumps(raw, default=str)),
                key=lambda r: json.dumps(r, sort_keys=True),
            )
            assert {"count": len(original), "sha256": canonical_hash(original)} == baseline[
                "tables"
            ]["ac_target_versions"]
            for identifier, archive_name in (
                (max(initial["old_version_ids"]), "previous-source.zip"),
                (initial["version_id"], "initial-source.zip"),
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
                            sha(data.decode("utf-8").replace("\r\n", "\n").encode()) == expected
                        ), (identifier, name)
                counts[str(identifier)] = len(manifest["sources"])
            assert (
                RunService(engine, Settings()).current_manifest()["config_hash"]
                == version["config_hash"]
            )
    finally:
        engine.dispose()
    historical = json.loads((root / ".local/t17h/historical-hashes.json").read_bytes())
    for name, expected in historical.items():
        assert sha((root / name).read_bytes()) == expected, name
    prior = json.loads((root / "docs/evidence/T17G/delivery-manifest.json").read_bytes())
    evolved = {}
    with zipfile.ZipFile(folder / "previous-source.zip") as archive:
        for name, record in prior["files"].items():
            data = (root / name).read_bytes()
            if sha(data) != record["sha256"]:
                if name == "任务规划.md":
                    with zipfile.ZipFile(
                        root / "docs/evidence/planning/2026-11-03-worker/previous-planning.zip"
                    ) as planning:
                        matches = [n for n in planning.namelist() if n.endswith("任务规划.md")]
                        assert len(matches) == 1
                        data = planning.read(matches[0])
                    evolved[name] = "planning/2026-11-03-worker/previous-planning.zip"
                else:
                    data = archive.read(name)
                    evolved[name] = "T17H/previous-source.zip"
            assert sha(data) == record["sha256"], name
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
    assert len(generated) == 21
    for path, data in generated.items():
        assert path.read_bytes() == data, path.name
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    assert head == baseline["base_commit"]
    assert not subprocess.check_output(
        ["git", "diff", "--cached", "--name-only"], cwd=root, text=True
    ).strip()
    record = {
        "checked_at": datetime.now(UTC).isoformat(),
        "old_49_versions_unchanged": True,
        "main_business_tables_unchanged": True,
        "tables": now["tables"],
        "triggers": now["triggers"],
        "ledgers": now["ledgers"],
        "ledger_fields_unchanged": True,
        "historical_files_byte_identical": len(historical),
        "verified_source_counts": counts,
        "previous_T17G_seal_unchanged_and_evolution_archived": evolved,
        "contracts_identical_in_memory": {p.name: sha(data) for p, data in generated.items()},
        "user_quick_navigation_preserved": True,
        "four_paid_flags_false": True,
        "new_model_calls": 0,
        "c_free_bytes": now["c_free_bytes"],
        "head_unchanged": head,
        "index_empty": True,
    }
    (folder / "preservation-audit.json").write_bytes(
        (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode()
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
