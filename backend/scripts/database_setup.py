"""Explicit fresh-database SQL import; no models, Redis, cloud deployment or data deletion."""

import argparse
import json

from database_bundle import finalize_demo, import_schema, prepare_demo, verify_demo

from app.core.config import Settings
from app.db.engine import create_database_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("import-schema", "prepare-demo", "finalize-demo", "verify")
    )
    parser.add_argument("--empty-database", action="store_true")
    parser.add_argument("--reference-data", action="store_true")
    args = parser.parse_args()
    if args.action == "import-schema" and not args.empty_database:
        parser.error("import-schema requires --empty-database and an actually empty database")
    if args.action in ("prepare-demo", "finalize-demo") and not args.reference_data:
        parser.error(
            "Demo preparation requires --reference-data; developer seeds are not independent labels"
        )
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        if args.action == "import-schema":
            import_schema(engine)
            result = {"schema_imported": True, "next": "prepare-demo --reference-data"}
        elif args.action == "prepare-demo":
            result = prepare_demo(engine, settings)
        elif args.action == "finalize-demo":
            result = finalize_demo(engine, settings)
        else:
            result = verify_demo(engine, settings)
        print(json.dumps({"database": engine.url.database, **result}, ensure_ascii=False, indent=2))
    except Exception as error:
        # DBAPI exception strings may contain connection parameters or SQL values.
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(error).__name__,
                    "detail": str(error)
                    if isinstance(error, ValueError)
                    else "Database operation failed; inspect the selected database and server log. "
                    "No automatic cleanup was performed.",
                },
                ensure_ascii=False,
            )
        )
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
