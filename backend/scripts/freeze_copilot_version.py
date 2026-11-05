"""Append the current compatibility version without altering old evidence/versions."""

import argparse
import hashlib
import json
import zipfile
from datetime import UTC, datetime

from app.agents.versions import register_version
from app.copilot.service import configuration
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.executors.service import RunService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    folder = PROJECT_ROOT / "docs/evidence/T17E"
    record_name = "final-main-version.json" if args.final else "main-version.json"
    archive_name = "frozen-source-final.zip" if args.final else "frozen-source.zip"
    assert not (folder / record_name).exists()
    settings = Settings()
    assert not settings.copilot_allow_paid_model_calls
    engine = create_database_engine(settings)
    try:
        manifest = RunService(engine, settings).current_manifest()
        identifier = register_version(engine, manifest)
        if not (folder / archive_name).exists():
            with zipfile.ZipFile(folder / archive_name, "w", zipfile.ZIP_DEFLATED) as archive:
                for source_name in manifest["sources"]:
                    archive.write(PROJECT_ROOT / source_name, source_name)
        with zipfile.ZipFile(folder / archive_name) as archive:
            assert archive.testzip() is None
            assert set(archive.namelist()) == set(manifest["sources"])
            for source_name, expected in manifest["sources"].items():
                data = archive.read(source_name).decode("utf-8").replace("\r\n", "\n").encode()
                assert hashlib.sha256(data).hexdigest() == expected
        record = {
            "frozen_at": datetime.now(UTC).isoformat(),
            "version_id": identifier,
            "config_hash": manifest["config_hash"],
            "source_count": len(manifest["sources"]),
            "copilot_configuration": configuration(settings),
            "manifest": manifest,
            "boundary": "T17E compatibility and assistant protocol; "
            "not an Agent improvement experiment. Old versions preserved.",
        }
        (folder / record_name).write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "version_id": identifier,
                    "source_count": len(manifest["sources"]),
                    "config_hash": manifest["config_hash"],
                }
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
