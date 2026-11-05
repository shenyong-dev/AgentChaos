"""Append T17F source compatibility, keeping every existing version unchanged."""

import json
from datetime import UTC, datetime

from app.agents.versions import register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.executors.service import RunService
from scripts.freeze_judge_version import rows
from scripts.prepare_sessions import observe


def main():
    folder = PROJECT_ROOT / "docs/evidence/T17F"
    output = folder / "main-version.json"
    baseline = json.loads((folder / "preparation.json").read_bytes())
    before = observe()
    for table, expected in baseline["tables"].items():
        if table != "ac_target_versions":
            assert before["tables"][table] == expected
    assert before["ledgers"] == baseline["ledgers"]
    engine = create_database_engine(Settings())
    try:
        old = rows(engine)
        ids = [row["id"] for row in old]
        if output.exists():
            initial = json.loads((folder / "initial-main-version.json").read_bytes())
            assert ids == initial["old_version_ids"] + [initial["version_id"]]
            assert json.loads(output.read_bytes()) == initial
        manifest = RunService(engine, Settings()).current_manifest()
        version = register_version(engine, manifest)
        assert version not in ids and rows(engine, ids) == old
        record = {
            "checked_at": datetime.now(UTC).isoformat(),
            "version_id": version,
            "config_hash": manifest["config_hash"],
            "old_version_ids": ids,
            "old_versions_unchanged": True,
            "new_model_calls": 0,
            "kind": "T17F browser session/source compatibility; no new Agent experiment",
            "initial_version_record": "initial-main-version.json" if output.exists() else None,
        }
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"version_id": version, "config_hash": manifest["config_hash"]}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
