"""Read-only T02 schema/seed evidence. Never emits credentials or password hashes."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, get_settings
from app.core.passwords import verify_password
from app.db.engine import create_database_engine
from app.models import Base, User


def collect_state():
    settings = get_settings()
    engine = create_database_engine(settings)
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
        tables = {}
        with engine.connect() as connection:
            version = connection.scalar(text("SELECT VERSION()"))
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            timezone = connection.scalar(text("SELECT @@session.time_zone"))
            isolation = connection.scalar(text("SELECT @@transaction_isolation"))
            lock_wait = connection.scalar(text("SELECT @@innodb_lock_wait_timeout"))
            for name, table in sorted(Base.metadata.tables.items()):
                foreign_keys = inspector.get_foreign_keys(name)
                checks = inspector.get_check_constraints(name)
                unique = inspector.get_unique_constraints(name)
                indexes = inspector.get_indexes(name)
                for kind, actual in (
                    (ForeignKeyConstraint, foreign_keys),
                    (CheckConstraint, checks),
                    (UniqueConstraint, unique),
                ):
                    expected = {item.name for item in table.constraints if isinstance(item, kind)}
                    assert expected <= {item["name"] for item in actual}, (name, kind.__name__)
                assert {item.name for item in table.indexes} <= {item["name"] for item in indexes}
                tables[name] = {
                    "options": inspector.get_table_options(name),
                    "rows": connection.scalar(text(f"SELECT COUNT(*) FROM `{name}`")),
                    "foreign_keys": foreign_keys,
                    "checks": checks,
                    "unique_constraints": unique,
                    "indexes": indexes,
                }
            triggers = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT trigger_name AS name, event_manipulation AS event, "
                        "event_object_table AS target_table, action_statement AS body "
                        "FROM information_schema.triggers "
                        "WHERE trigger_schema=DATABASE() ORDER BY trigger_name"
                    )
                ).mappings()
            ]
            assert len(triggers) == 23
            for trigger in triggers:
                body = trigger.pop("body")
                trigger["definition_sha256"] = hashlib.sha256(body.encode()).hexdigest()
            temporary_databases = list(
                connection.scalars(
                    text(
                        "SELECT schema_name FROM information_schema.schemata "
                        "WHERE schema_name REGEXP '^agentchaos_t[0-9]{2}_verify_[0-9a-f]{12}$'"
                    )
                )
            )
        with Session(engine) as session:
            accounts = []
            for user in session.scalars(select(User).order_by(User.username)):
                accounts.append(
                    {
                        "username": user.username,
                        "role": user.role,
                        "salted_hash": user.password_hash.startswith("pbkdf2_sha256$"),
                        "matches_local_seed_credential": verify_password(
                            settings.demo_seed_password.get_secret_value(), user.password_hash
                        ),
                    }
                )
        migration_directory = ScriptDirectory.from_config(
            Config(str(PROJECT_ROOT / "backend/alembic.ini"))
        )
        migration = Path(migration_directory.get_revision(revision).path)
        return {
            "checked_at": datetime.now(UTC).isoformat(),
            "mysql_version": version,
            "database": settings.mysql_database,
            "revision": revision,
            "migration_sha256": hashlib.sha256(migration.read_bytes()).hexdigest(),
            "migration_files": {
                Path(item.path).name: hashlib.sha256(Path(item.path).read_bytes()).hexdigest()
                for item in migration_directory.walk_revisions()
            },
            "session_time_zone": timezone,
            "transaction_isolation": isolation,
            "innodb_lock_wait_timeout_seconds": lock_wait,
            "domain_table_count": len(tables),
            "alembic_version_table_count": 1,
            "tables": tables,
            "triggers": triggers,
            "accounts": accounts,
            "remaining_temporary_databases": temporary_databases,
            "verification": "Declared table, foreign-key, CHECK, unique and index names exist",
            "limitations": "Alembic check does not verify trigger bodies or all CHECK changes",
        }
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args()
    result = collect_state()
    if args.junit:
        root = ET.parse(args.junit).getroot()
        cases = root.findall(".//testcase")
        assert cases and all(
            case.find("failure") is None
            and case.find("error") is None
            and case.find("skipped") is None
            for case in cases
        ), "Evidence requires a fully passing integration run"
        result["pytest"] = {
            "passed": len(cases),
            "failed": 0,
            "skipped": 0,
            "junit_sha256": hashlib.sha256(args.junit.read_bytes()).hexdigest(),
            "tests": [
                {
                    "class": case.get("classname"),
                    "name": case.get("name"),
                    "seconds": float(case.get("time", "0")),
                }
                for case in cases
            ],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Saved read-only evidence: {result['domain_table_count']} domain tables, "
        f"{len(result['triggers'])} triggers."
    )


if __name__ == "__main__":
    main()
