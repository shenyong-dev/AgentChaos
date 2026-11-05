"""Append source compatibility for T17G, without altering prior versions."""

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
    folder = PROJECT_ROOT / "docs/evidence/T17G"
    output = folder / "main-version.json"
    assert not output.exists()
    baseline = json.loads((folder / "preparation.json").read_bytes())
    before = observe()
    assert before["tables"] == baseline["tables"]
    assert before["ledgers"] == baseline["ledgers"]
    engine = create_database_engine(Settings())
    try:
        old = rows(engine)
        ids = [row["id"] for row in old]
        manifest = RunService(engine, Settings()).current_manifest()
        with zipfile.ZipFile(folder / "frozen-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for path in manifest["sources"]:
                archive.write(PROJECT_ROOT / path, path)
        version = register_version(engine, manifest)
        assert version not in ids and rows(engine, ids) == old
        record = {
            "checked_at": datetime.now(UTC).isoformat(),
            "version_id": version,
            "config_hash": manifest["config_hash"],
            "old_version_ids": ids,
            "old_versions_unchanged": True,
            "source_count": len(manifest["sources"]),
            "new_model_calls": 0,
            "kind": "T17G deterministic development-case reading aid; not a new Agent experiment",
        }
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"version_id": version, "config_hash": manifest["config_hash"]}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
