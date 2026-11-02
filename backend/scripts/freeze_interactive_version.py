"""Append current source compatibility only; preserve every prior main version."""

import json
from datetime import UTC, datetime

from app.agents.versions import canonical_hash, register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.executors.service import RunService
from scripts.freeze_judge_version import rows


def main():
    output = PROJECT_ROOT / "docs/evidence/T17D/main-version.json"
    assert not output.exists()
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
    baseline = json.loads((output.parent / "preparation.json").read_bytes())
    engine = create_database_engine(settings)
    try:
        before = rows(engine)
        assert (
            canonical_hash(json.loads(json.dumps(before, default=str)))
            == baseline["tables"]["ac_target_versions"]["sha256"]
        )
        manifest = RunService(engine, settings).current_manifest()
        identifier = register_version(engine, manifest)
        assert identifier not in baseline["old_version_ids"]
        assert rows(engine, baseline["old_version_ids"]) == before
        output.write_text(
            json.dumps(
                {
                    "checked_at": datetime.now(UTC).isoformat(),
                    "version_id": identifier,
                    "config_hash": manifest["config_hash"],
                    "old_version_ids": baseline["old_version_ids"],
                    "old_versions_unchanged": True,
                    "kind": (
                        "interactive UI/ownership/database binding compatibility; "
                        "development model outcomes are separate"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"version_id": identifier, "config_hash": manifest["config_hash"]}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
