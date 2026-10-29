"""Inspect or explicitly register the current source-compatible V0, without model calls."""

import argparse
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.versions import register_version
from app.core.config import Settings
from app.db.engine import create_database_engine
from app.executors.service import RunService
from app.models import TargetVersion


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", action="store_true")
    args = parser.parse_args()
    engine = create_database_engine(Settings())
    try:
        manifest = RunService(engine, Settings()).current_manifest()
        if args.register:
            identifier = register_version(engine, manifest)
        else:
            with Session(engine) as session:
                identifier = session.scalar(
                    select(TargetVersion.id).where(
                        TargetVersion.config_hash == manifest["config_hash"]
                    )
                )
        print(
            json.dumps(
                {
                    "target_version_id": identifier,
                    "config_hash": manifest["config_hash"],
                    "baseline": "V0",
                    "provider_calls": 0,
                    "existing_versions_modified": 0,
                    "boundary": (
                        "Source compatibility registration only; "
                        "no new Agent quality validation or V1."
                    ),
                }
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
