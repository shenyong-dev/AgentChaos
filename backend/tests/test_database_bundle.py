"""SQL deliverable import into disposable MySQL; no Redis or model calls."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database

from app.core.config import Settings
from app.core.passwords import verify_password
from app.db.seeds import seed_database
from app.models import User
from scripts.database_bundle import (
    DIRECTORY,
    bundle_files,
    finalize_demo,
    import_schema,
    prepare_demo,
    reference_cases,
    statements,
    verify_demo,
)


def test_generated_bundle_is_current_and_contains_no_accounts_or_history():
    for name, content in bundle_files().items():
        assert (DIRECTORY / name).read_bytes() == content.encode()
    schema = (DIRECTORY / "schema.sql").read_text("utf-8")
    parsed = list(statements(schema))
    assert any(s.startswith("CREATE TRIGGER") and "BEGIN" in s and "END IF;" in s for s in parsed)
    assert all("DELIMITER" not in s for s in parsed)
    data = (DIRECTORY / "demo_data.sql").read_text("utf-8")
    assert "INSERT INTO ac_users" not in data and "password_hash" not in data
    assert "INSERT INTO ac_test_runs" not in data and "INSERT INTO sb_bookings" not in data
    assert len(reference_cases()) == 12
    assert {r["dataset_split"] for r in reference_cases()} == {"dev"}
    assert not any(r["case_key"].startswith("T13-") for r in reference_cases())
    assert not any("independent-label" == r["source"] for r in reference_cases())


def test_malformed_sql_bundle_is_rejected():
    with pytest.raises(ValueError):
        list(statements("SELECT 1\nDELIMITER $$"))
    with pytest.raises(ValueError):
        list(statements("SELECT 1"))


@pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="disposable MySQL")
def test_real_sql_import_matches_migrations_seed_and_actual_workbench():
    settings = Settings().model_copy(update={"demo_seed_password": TEST_PASSWORD})
    # model_copy bypasses conversion, so construct the public secret type explicitly.
    from pydantic import SecretStr

    settings.demo_seed_password = SecretStr(TEST_PASSWORD)
    with disposable_database("T17J") as engine:
        import_schema(engine)
        assert len(inspect(engine).get_table_names()) == 17
        with pytest.raises(ValueError, match="not empty"):
            import_schema(engine)
        report = prepare_demo(engine, settings)
        assert report == {
            "schema_revision": "20261107_0007",
            "targets": 2,
            "development_cases": 12,
            "fault_profiles": 6,
            "model_calls": 0,
        }
        with Session(engine) as session:
            assert all(
                verify_password(TEST_PASSWORD, u.password_hash)
                for u in session.scalars(select(User))
            )
        with pytest.raises(ValueError, match="no history/reference"):
            prepare_demo(engine, settings)
        assert finalize_demo(engine, settings) == report
        assert verify_demo(engine, settings) == report
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM ac_test_runs")) == 0
            assert connection.scalar(text("SELECT COUNT(*) FROM ac_trace_events")) == 0
            trigger_count = connection.scalar(
                text(
                    "SELECT COUNT(*) FROM information_schema.triggers "
                    "WHERE trigger_schema=DATABASE()"
                )
            )
            assert trigger_count >= 20


@pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="disposable MySQL")
def test_mysql_client_manual_path_and_actual_cli_checks():
    from pydantic import SecretStr

    client = Path(
        os.getenv("AC_MYSQL_CLIENT", r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")
    )
    if not client.is_file():
        pytest.skip("Set AC_MYSQL_CLIENT to the installed mysql command-line client")
    settings = Settings().model_copy(update={"demo_seed_password": SecretStr(TEST_PASSWORD)})
    with disposable_database("T17J") as engine:
        environment = {**os.environ, "MYSQL_PWD": settings.mysql_password.get_secret_value()}
        command = [
            str(client),
            "--no-defaults",
            "--batch",
            "--default-character-set=utf8mb4",
            f"--host={settings.mysql_host}",
            f"--port={settings.mysql_port}",
            f"--user={settings.mysql_user}",
            f"--database={engine.url.database}",
        ]
        for file in ("schema.sql", "demo_data.sql"):
            if file == "demo_data.sql":
                seed_database(engine, TEST_PASSWORD)
            result = subprocess.run(
                command,
                input=(DIRECTORY / file).read_bytes(),
                env=environment,
                capture_output=True,
                timeout=45,
                check=False,
            )
            assert result.returncode == 0, f"mysql client failed to import {file}"
        assert finalize_demo(engine, settings)["development_cases"] == 12
        cli_environment = {
            **os.environ,
            "MYSQL_DATABASE": engine.url.database,
            "DEMO_SEED_PASSWORD": TEST_PASSWORD,
        }
        cli = [sys.executable, "scripts/database_setup.py"]
        result = subprocess.run(
            [*cli, "verify"],
            cwd=DIRECTORY.parent / "backend",
            env=cli_environment,
            capture_output=True,
            timeout=45,
            check=False,
        )
        assert result.returncode == 0 and b'"development_cases": 12' in result.stdout
        rejected = subprocess.run(
            [*cli, "import-schema", "--empty-database"],
            cwd=DIRECTORY.parent / "backend",
            env=cli_environment,
            capture_output=True,
            timeout=45,
            check=False,
        )
        assert rejected.returncode == 1 and b"not empty" in rejected.stdout
        assert verify_demo(engine, settings)["targets"] == 2
