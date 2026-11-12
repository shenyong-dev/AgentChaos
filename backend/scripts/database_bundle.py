"""Build and import the public, empty-database SQL bundle without historical data."""

import hashlib
import io
import json
import re

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.adapters.refund_catalog import CASES, register
from app.agents.versions import canonical_hash, register_version
from app.core.config import PROJECT_ROOT
from app.db.seeds import seed_database
from app.executors.service import RunService, case_content, frozen_case
from app.faults.service import profile_spec
from app.models import Base, FaultProfile, TestCase, User
from app.schemas.faults import FaultSpec
from app.workbench.service import WorkbenchService

DIRECTORY = PROJECT_ROOT / "database"
BOOKING_KEYS = ("T13-N001", "T13-N002", "T13-N005", "T13-N008")
REFERENCE_SOURCE = "project_reference:SQL-demo-not-independent-human-labels"


def revision():
    return ScriptDirectory.from_config(
        Config(str(PROJECT_ROOT / "backend/alembic.ini"))
    ).get_current_head()


def reference_cases():
    # Public development inputs only. No local database, calibration or holdout export.
    catalog = json.loads((PROJECT_ROOT / "datasets/v1.1/candidate.json").read_text("utf-8"))
    rows = []
    for key in BOOKING_KEYS:
        item = next(c for c in catalog["cases"] if c["id"] == key)
        draft = item["dsl"]
        if draft["dataset_split"] != "dev" or item["knowledge_profile"] != "base":
            raise ValueError("SQL demo inputs must be public base-knowledge development cases")
        rows.append(
            {
                "case_key": "DEMO-" + key,
                "revision": 1,
                "family_key": "SQL-DEMO-" + draft["family_key"],
                "category": draft["category"],
                "dataset_split": "dev",
                "source": REFERENCE_SOURCE,
                "turns_json": draft["turns"],
                "fixture_json": {
                    "fixture": draft["fixture"],
                    "reference_clock": draft["reference_clock"],
                    "reference_source": key,
                    "independent_human_labels": False,
                },
                "expected_json": {
                    "schema_version": "1.1",
                    "assertions": draft["assertions"],
                    "task_goal": draft.get("task_goal"),
                },
            }
        )
    for key, title, turns, count, total, order, fault in CASES:
        rows.append(
            {
                "case_key": "T18-" + key,
                "revision": 1,
                "family_key": "T18-reference-" + key.lower(),
                "category": "normal"
                if key == "NORMAL"
                else "security"
                if key in ("OWNER", "CHANGE", "DECLINE")
                else "tool_execution",
                "dataset_split": "dev",
                "source": "project_reference:T18-not-independent-human-labels",
                "turns_json": [{"user": t} for t in turns],
                "fixture_json": {
                    "fixture": "refund-reference-v1",
                    "reference_clock": "2026-11-07T01:00:00+00:00",
                    "reference_title": title,
                    "declared_fault": fault,
                },
                "expected_json": {
                    "schema_version": "refund-1",
                    "assertions": [
                        {
                            "type": "refund_ledger",
                            "count": count,
                            "total_fen": total,
                            "order_id": order,
                        }
                    ],
                },
            }
        )
    for row in rows:
        record = TestCase(**row)
        row["content_hash"] = canonical_hash(case_content(record))
    return rows


def reference_faults():
    return [
        FaultSpec(
            profile_key=key,
            fault_type=kind,
            trigger_stage=stage,
            target_tool=tool,
            seed=20261112,
        ).model_dump(mode="json")
        for key, kind, stage, tool in (
            ("DEMO-BOOKING-F01", "F01", "PRE_DISPATCH", "confirm_booking"),
            ("DEMO-BOOKING-F02", "F02", "PRE_COMMIT", "confirm_booking"),
            ("DEMO-BOOKING-F03", "F03", "POST_RESULT", "query_availability"),
            ("DEMO-BOOKING-F04", "F04", "POST_COMMIT", "confirm_booking"),
            ("DEMO-REFUND-F01", "F01", "PRE_DISPATCH", "confirm_refund"),
            ("DEMO-REFUND-F04", "F04", "POST_COMMIT", "confirm_refund"),
        )
    ]


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    # Hex UTF-8 is independent of client encoding and NO_BACKSLASH_ESCAPES.
    return "CONVERT(0x" + value.encode("utf-8").hex() + " USING utf8mb4)"


def bundle_files():
    output = io.StringIO()
    config = Config(str(PROJECT_ROOT / "backend/alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    schema = "\n".join(line.rstrip() for line in output.getvalue().splitlines()).strip() + "\n"
    # MySQL clients require DELIMITER around compound trigger bodies.
    schema = re.sub(
        r"(?ms)^CREATE TRIGGER .*?(?=\n\n|\Z)",
        lambda m: "DELIMITER $$\n" + m.group().rstrip().removesuffix(";") + "$$\nDELIMITER ;",
        schema,
    )
    header = (
        "-- AgentChaos public schema; generated from all Alembic upgrades.\n"
        "-- EMPTY MySQL 8 database only. Never import into the local/cloud working database.\n"
        "-- No CREATE DATABASE, USE, account passwords or historical records.\n"
        f"-- Schema revision: {revision()}\n"
        "SET NAMES utf8mb4;\nSET SESSION time_zone = '+00:00';\n\n"
    )
    cases = reference_cases()
    data = [
        "-- Optional developer reference data, NOT independent human annotations or test results.",
        "-- Run seed_database with your private DEMO_SEED_PASSWORD before importing this file.",
        "-- One-time import into a fresh seeded database. No INSERT IGNORE/REPLACE/updates.",
        "SET NAMES utf8mb4;",
        "SET SESSION time_zone = '+00:00';",
        "SET @ac_demo_reviewer = (SELECT id FROM ac_users "
        "WHERE username='demo_reviewer' AND role='reviewer' AND enabled=1);",
        "START TRANSACTION;",
    ]
    for row in cases:
        names = [*row, "review_status", "reviewer_id", "reviewed_at"]
        values = [
            *(literal(v) for v in row.values()),
            "'approved'",
            "@ac_demo_reviewer",
            "UTC_TIMESTAMP(6)",
        ]
        data += [
            "-- Reference case " + row["case_key"],
            "INSERT INTO ac_test_cases (" + ",".join(names) + ")",
            "VALUES (" + ",".join(values) + ");",
        ]
    for fault in reference_faults():
        row = {("parameters_json" if k == "parameters" else k): v for k, v in fault.items()}
        data += [
            "INSERT INTO ac_fault_profiles (" + ",".join(row) + ")",
            "VALUES (" + ",".join(literal(v) for v in row.values()) + ");",
        ]
    data += ["COMMIT;", ""]
    verify = """-- Read-only SQL checks after schema import and prepare-demo.
SELECT DATABASE() AS selected_database, VERSION() AS mysql_version;
SELECT version_num FROM alembic_version;
SELECT username, role, enabled FROM ac_users ORDER BY id;
SELECT adapter_type, COUNT(*) AS targets FROM ac_targets GROUP BY adapter_type;
SELECT case_key, dataset_split, review_status, source FROM ac_test_cases ORDER BY id;
SELECT fault_type, trigger_stage, target_tool FROM ac_fault_profiles ORDER BY id;
SELECT COUNT(*) AS runs_expected_zero FROM ac_test_runs;
SELECT COUNT(*) AS executions_expected_zero FROM ac_test_executions;
SELECT COUNT(*) AS bookings_expected_zero FROM sb_bookings;
"""
    files = {"schema.sql": header + schema, "demo_data.sql": "\n".join(data), "verify.sql": verify}
    migrations = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((PROJECT_ROOT / "backend/alembic/versions").glob("*.py"))
    }
    manifest = {
        "format": "agentchaos-public-sql-v1",
        "schema_revision": revision(),
        "mysql_verified": "8.0.39",
        "tables_including_alembic": len(Base.metadata.tables) + 1,
        "reference_cases": {"booking": 4, "refund": 8},
        "fault_profiles": 6,
        "accounts_in_sql": 0,
        "history_or_independent_labels_in_sql": False,
        "files": {
            name: hashlib.sha256(value.encode()).hexdigest() for name, value in files.items()
        },
        "migration_sources": migrations,
    }
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    return files


def checked_sql(name):
    manifest = json.loads((DIRECTORY / "manifest.json").read_text("utf-8"))
    if manifest["schema_revision"] != revision():
        raise ValueError("SQL revision differs from current migrations; regenerate the SQL bundle")
    for path, digest in manifest["migration_sources"].items():
        if (
            hashlib.sha256(
                (PROJECT_ROOT / "backend/alembic/versions" / path).read_bytes()
            ).hexdigest()
            != digest
        ):
            raise ValueError("SQL migration source differs from its manifest")
    raw = (DIRECTORY / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["files"][name]:
        raise ValueError("SQL file hash differs from its manifest")
    return raw.decode("utf-8")


def statements(sql):
    """Read this generated bundle's line-ended statements and DELIMITER blocks."""
    delimiter, lines = ";", []
    for line in sql.splitlines():
        if line.lstrip().startswith("--") or not line.strip():
            continue
        if line.startswith("DELIMITER "):
            if lines:
                raise ValueError("Unterminated SQL before DELIMITER")
            delimiter = line.split(maxsplit=1)[1]
            continue
        lines.append(line)
        if line.rstrip().endswith(delimiter):
            yield "\n".join(lines).rstrip()[: -len(delimiter)]
            lines = []
    if lines or delimiter != ";":
        raise ValueError("Unterminated SQL bundle")


def import_schema(engine):
    sql = checked_sql("schema.sql")
    if inspect(engine).get_table_names():
        raise ValueError(
            "Database is not empty; schema import refused. Use migrations for existing data"
        )
    with engine.connect() as connection:
        # MySQL DDL commits independently. Errors must be repaired in a new empty database.
        for statement in statements(sql):
            connection.exec_driver_sql(statement)
        connection.commit()


def assert_demo_database(engine):
    tables = set(inspect(engine).get_table_names())
    if tables != set(Base.metadata.tables) | {"alembic_version"}:
        raise ValueError("Database schema is incomplete or contains unrelated tables")
    with engine.connect() as connection:
        if connection.scalar(text("SELECT version_num FROM alembic_version")) != revision():
            raise ValueError("Database migration revision does not match this SQL bundle")
        permitted = {
            "ac_users",
            "ac_targets",
            "sb_rooms",
            "ac_knowledge_documents",
            "alembic_version",
        }
        if any(connection.scalar(text(f"SELECT COUNT(*) FROM `{t}`")) for t in tables - permitted):
            raise ValueError(
                "Reference preparation requires a fresh schema with no history/reference data"
            )
        users = connection.execute(text("SELECT username FROM ac_users")).scalars().all()
        if set(users) - {"demo_operator", "demo_reviewer"}:
            raise ValueError("Unrelated accounts exist; demo preparation refused")


def prepare_demo(engine, settings):
    sql = checked_sql("demo_data.sql")
    assert_demo_database(engine)
    seed_database(engine, settings.demo_seed_password.get_secret_value())
    with engine.connect() as connection:
        for statement in statements(sql):
            connection.exec_driver_sql(statement)
        connection.commit()
    return finalize_demo(engine, settings)


def validate_demo_data(engine, *, require_unused=False):
    if require_unused:
        with engine.connect() as connection:
            for table in ("ac_test_runs", "ac_test_executions", "ac_trace_events", "sb_bookings"):
                if connection.scalar(text(f"SELECT COUNT(*) FROM `{table}`")):
                    raise ValueError(
                        "This is not an unused demo database; no history may be overwritten"
                    )
    with Session(engine) as session:
        rows = list(session.scalars(select(TestCase).order_by(TestCase.id)))
        expected = {r["case_key"]: r for r in reference_cases()}
        for row in rows:
            if row.case_key in expected:
                if (
                    row.content_hash != expected[row.case_key]["content_hash"]
                    or row.review_status != "approved"
                ):
                    raise ValueError("Installed reference case differs from the distributed source")
                frozen_case(row)
        if set(expected) != {r.case_key for r in rows}:
            raise ValueError(
                "Expected exactly 12 distributed reference cases in a fresh demo database"
            )
        actual_faults = {
            r.profile_key: profile_spec(r) for r in session.scalars(select(FaultProfile))
        }
        if actual_faults != {r["profile_key"]: r for r in reference_faults()}:
            raise ValueError("Expected exactly six matching developer reference fault profiles")
        users = list(session.scalars(select(User)))
        if {(u.username, u.role, u.enabled) for u in users} != {
            ("demo_operator", "operator", True),
            ("demo_reviewer", "reviewer", True),
        }:
            raise ValueError("Demo accounts are missing or differ from the expected roles")
        owner = session.scalar(select(User.id).where(User.username == "demo_operator"))
    return owner


def finalize_demo(engine, settings):
    owner = validate_demo_data(engine, require_unused=True)
    register_version(engine, RunService(engine, settings).current_manifest())
    register(engine, settings, owner=owner)
    return verify_demo(engine, settings)


def verify_demo(engine, settings):
    owner = validate_demo_data(engine)
    service = WorkbenchService(engine, settings)
    overview = service.overview(owner)
    if overview["counts"]["development_cases"] != 12 or overview["counts"]["targets"] != 2:
        raise ValueError("Demo objects/cases are not visible in the actual workbench")
    if len(overview["versions"]["items"]) != 2 or not all(
        v["compatible"] for v in overview["versions"]["items"]
    ):
        raise ValueError(
            "Register versions against the currently installed source and configuration"
        )
    config = Config(str(PROJECT_ROOT / "backend/alembic.ini"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.check(config)
    return {
        "schema_revision": revision(),
        "targets": 2,
        "development_cases": 12,
        "fault_profiles": 6,
        "model_calls": 0,
    }
