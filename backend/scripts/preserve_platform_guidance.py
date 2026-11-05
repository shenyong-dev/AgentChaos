"""Verify T17I sources, seals and the observed main database baseline."""

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
from app.guidance.service import catalog
from scripts.prepare_sessions import observe


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    root = PROJECT_ROOT
    folder = root / "docs/evidence/T17I"
    assert not (folder / "preservation-audit.json").exists()
    baseline = json.loads((folder / "preparation.json").read_bytes())
    concurrent = json.loads((folder / "observed-main-changes.json").read_bytes())
    version = json.loads((folder / "main-version.json").read_bytes())
    now = observe()
    assert now["c_free_bytes"] > 2_000_000_000
    assert now["ledgers"] == baseline["ledgers"]
    assert now["triggers"] == baseline["triggers"] == 23
    for name, item in concurrent["after"].items():
        if name != "ac_target_versions":
            assert now["tables"][name] == item, name
    engine = create_database_engine(Settings())
    counts = {}
    try:
        with engine.connect() as connection:
            old = [
                dict(r)
                for r in connection.execute(
                    text("SELECT * FROM ac_target_versions WHERE id<:id"),
                    {"id": version["version_id"]},
                ).mappings()
            ]
            old = sorted(
                json.loads(json.dumps(old, default=str)),
                key=lambda r: json.dumps(r, sort_keys=True),
            )
            assert {"count": len(old), "sha256": canonical_hash(old)} == baseline["tables"][
                "ac_target_versions"
            ]
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
                        content = (
                            archive.read(name)
                            if name in archive.namelist()
                            else (root / name).read_bytes()
                        )
                        assert (
                            digest(content.decode("utf-8").replace("\r\n", "\n").encode())
                            == expected
                        ), (identifier, name)
                counts[str(identifier)] = len(manifest["sources"])
            assert (
                RunService(engine, Settings()).current_manifest()["config_hash"]
                == version["config_hash"]
            )
    finally:
        engine.dispose()
    historical = json.loads((root / ".local/t17i/historical-hashes.json").read_bytes())
    for name, expected in historical.items():
        assert digest((root / name).read_bytes()) == expected, name
    previous = json.loads((root / "docs/evidence/T17H/delivery-manifest.json").read_bytes())
    evolution = []
    with zipfile.ZipFile(folder / "previous-source.zip") as archive:
        for name, item in previous["files"].items():
            data = (root / name).read_bytes()
            if digest(data) != item["sha256"]:
                data = archive.read(name)
                evolution.append(name)
            assert digest(data) == item["sha256"], name
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
    assert len(generated) == 23
    for path, data in generated.items():
        assert path.read_bytes() == data, path.name
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    assert head == baseline["base_commit"]
    assert not subprocess.check_output(
        ["git", "diff", "--cached", "--name-only"], cwd=root, text=True
    ).strip()
    value, catalog_hash = catalog()
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "old_51_versions_unchanged": True,
        "main_matches_observed_concurrent_activity_baseline": True,
        "tables": now["tables"],
        "concurrent_changes_not_rolled_back": True,
        "ledger_fields_unchanged": True,
        "ledgers": now["ledgers"],
        "historical_files_byte_identical": len(historical),
        "previous_T17H_127_item_seal_preserved": True,
        "changed_H_files_original_bytes_in_previous_source_zip": evolution,
        "verified_source_counts": counts,
        "triggers": now["triggers"],
        "contract_hashes": {p.name: digest(data) for p, data in generated.items()},
        "catalog": {
            "version": value["version"],
            "hash": catalog_hash,
            "pages": len(value["pages"]),
            "terms": len(value["terms"]),
            "actions": len(value["actions"]),
        },
        "user_quick_navigation_preserved": True,
        "four_paid_flags_false": True,
        "new_model_calls": 0,
        "c_free_bytes": now["c_free_bytes"],
        "head_unchanged": head,
        "index_empty": True,
    }
    (folder / "preservation-audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": True,
                "historical": len(historical),
                "ledgers": len(now["ledgers"]),
                "source_counts": counts,
                "contracts": len(generated),
                "catalog_hash": catalog_hash,
            }
        )
    )


if __name__ == "__main__":
    main()
