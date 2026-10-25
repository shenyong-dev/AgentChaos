"""Explicit T02 administration commands; reads credentials without printing them."""

import argparse
import json

from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import PROJECT_ROOT, get_settings
from app.db.engine import create_database_engine
from app.db.fixtures import clear_fixture, initialize_fixture
from app.db.seeds import seed_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("migrate", "check", "seed", "fixture-init", "fixture-clear", "inspect")
    )
    parser.add_argument("--scope")
    args = parser.parse_args()
    if args.action.startswith("fixture-") and not args.scope:
        parser.error("Fixture commands require an explicit --scope test:...")
    settings = get_settings()
    if args.action in ("migrate", "check"):
        config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
        if args.action == "migrate":
            command.upgrade(config, "head")
        else:
            command.check(config)
        return
    engine = create_database_engine(settings)
    try:
        if args.action == "seed":
            result = seed_database(engine, settings.demo_seed_password.get_secret_value())
        elif args.action.startswith("fixture-"):
            with Session(engine) as session, session.begin():
                result = (
                    initialize_fixture(session, args.scope)
                    if args.action == "fixture-init"
                    else clear_fixture(session, args.scope)
                )
        else:
            result = {"tables": inspect(engine).get_table_names()}
        print(json.dumps({"action": args.action, "result": result}, ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
