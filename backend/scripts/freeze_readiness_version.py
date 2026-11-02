"""Explicit free source registration; the readiness HTTP probe never registers versions."""

import json
from datetime import UTC, datetime

from app.agents.versions import canonical_hash, register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.executors.service import RunService
from scripts.freeze_judge_version import rows


def main():
    output = PROJECT_ROOT / "docs/evidence/T17C/main-version.json"
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
    engine = create_database_engine(settings)
    try:
        prior = rows(engine)
        ids = [row["id"] for row in prior]
        baseline = json.loads((output.parent / "preparation.json").read_bytes())
        assert (
            canonical_hash(json.loads(json.dumps(prior, default=str)))
            == baseline["tables"]["ac_target_versions"]["sha256"]
        )
        manifest = RunService(engine, settings).current_manifest()
        identifier = register_version(engine, manifest)
        assert identifier not in ids and rows(engine, ids) == prior
        output.write_text(
            json.dumps(
                {
                    "checked_at": datetime.now(UTC).isoformat(),
                    "version_id": identifier,
                    "config_hash": manifest["config_hash"],
                    "old_version_ids": ids,
                    "old_versions_unchanged": True,
                    "new_model_calls": 0,
                    "kind": (
                        "readiness and first-use guide; "
                        "source compatibility only, not Agent quality rerun"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"version_id": identifier, "hash": manifest["config_hash"]}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
