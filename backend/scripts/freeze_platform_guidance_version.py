"""Append help-source compatibility; preserve concurrent main actions and older versions."""

import json
import zipfile
from datetime import UTC, datetime

from app.agents.versions import register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.executors.service import RunService
from scripts.freeze_judge_version import rows
from scripts.prepare_sessions import observe


def main():
    folder = PROJECT_ROOT / "docs/evidence/T17I"
    assert not (folder / "main-version.json").exists()
    baseline = json.loads((folder / "preparation.json").read_bytes())
    concurrent = json.loads((folder / "observed-main-changes.json").read_bytes())
    observed = observe()
    assert observed["tables"] == concurrent["after"]
    assert observed["ledgers"] == baseline["ledgers"]
    engine = create_database_engine(Settings())
    try:
        old = rows(engine)
        manifest = RunService(engine, Settings()).current_manifest()
        assert "backend/app/guidance/catalog.json" in manifest["sources"]
        with zipfile.ZipFile(folder / "frozen-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name in manifest["sources"]:
                archive.write(PROJECT_ROOT / name, name)
        version = register_version(engine, manifest)
        assert rows(engine, [r["id"] for r in old]) == old
        record = {
            "checked_at": datetime.now(UTC).isoformat(),
            "version_id": version,
            "config_hash": manifest["config_hash"],
            "source_count": len(manifest["sources"]),
            "old_version_ids": [r["id"] for r in old],
            "old_versions_unchanged": True,
            "new_model_calls": 0,
            "boundary": "T17I deterministic help/navigation; not an Agent quality experiment. "
            "Concurrent cancellation/export retained in observed-main-changes.json.",
        }
        (folder / "main-version.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "version_id": version,
                    "source_count": len(manifest["sources"]),
                    "config_hash": manifest["config_hash"],
                }
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
