"""Narrow disposable MySQL lab, with round-trip checked local SQL archive."""

import hashlib
import json
import secrets
from uuid import uuid4

from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import inspect, text

from alembic import command
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.db.seeds import seed_database

from .common import AUTHORIZATION, disk_guard, guard, write_json


def table_hashes(engine):
    result = {}
    with engine.connect() as connection:
        for table in sorted(inspect(engine).get_table_names()):
            rows = [
                dict(row) for row in connection.execute(text(f"SELECT * FROM `{table}`")).mappings()
            ]
            rows = json.loads(json.dumps(rows, default=str))
            rows.sort(key=lambda r: json.dumps(r, sort_keys=True))
            result[table] = {"count": len(rows), "sha256": canonical_hash(rows)}
    return result


class Lab:
    def __init__(self, output, *, real=False):
        disk_guard()
        self.output, self.marker = output, uuid4().hex[:12]
        base = Settings()
        self.admin_settings = base
        self.database = "agentchaos_t17l_verify_" + self.marker
        self.user = "ac_t17l_" + self.marker
        password = secrets.token_urlsafe(32)
        self.settings = base.model_copy(
            update={
                "mysql_database": self.database,
                "mysql_user": self.user,
                "mysql_password": SecretStr(password),
                "executor_namespace": "ac:t17l:" + self.marker,
                "executor_allow_paid_model_calls": real,
                "executor_budget_authorization_id": AUTHORIZATION,
                "executor_agent_budget_usd": 1.0,
                "executor_agent_budget_unlimited": False,
                "model_timeout_seconds": 45,
                "agent_max_output_tokens": 1024,
                "agent_thinking": "disabled",
                "allow_paid_model_calls": False,
                "casegen_allow_paid_model_calls": False,
                "judge_allow_paid_model_calls": False,
                "copilot_allow_paid_model_calls": False,
            }
        )
        self.admin = create_database_engine(base, database="mysql")
        self.engine = create_database_engine(self.settings)
        guard(self.engine, self.settings)
        self.output.mkdir(parents=True, exist_ok=False)
        write_json(
            output / "ownership.json",
            {
                "database": self.database,
                "redis_namespace": self.settings.executor_namespace,
                "marker": self.marker,
                "sql_user": self.user,
                "real_provider": real,
                "cleanup": "only after SQL archive roundtrip matches",
            },
        )
        self.create(self.database)
        with self.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(
                text(f"CREATE USER '{self.user}'@'localhost' IDENTIFIED BY :password"),
                {"password": password},
            )
            for database in (self.database, self.database + "_restore"):
                c.exec_driver_sql(f"GRANT ALL ON `{database}`.* TO '{self.user}'@'localhost'")
        # The experiment connection has no global or main database grant.
        with self.engine.connect() as c:
            grants = [row[0] for row in c.exec_driver_sql("SHOW GRANTS")]
        if len(grants) != 3 or any(
            "GRANT USAGE ON *.*" not in grant
            and not any(
                f"`{name}`.*" in grant for name in (self.database, self.database + "_restore")
            )
            for grant in grants
        ):
            raise RuntimeError("Unexpected SQL grants; keep lab for inspection")
        write_json(
            output / "sql-isolation.json",
            {
                "user": self.user,
                "grants": grants,
                "global_privileges": "USAGE only",
                "main_database_grant": False,
            },
        )
        # MySQL binary logging requires elevated trigger creation. Only migrations
        # use the administrator, constrained to this generated lab DB. Agent/tool
        # operations below always use the separately granted experiment account.
        ddl_engine = create_database_engine(base, database=self.database)
        guard(ddl_engine)
        try:
            with ddl_engine.connect() as connection:
                config = Config(str(PROJECT_ROOT / "backend/alembic.ini"))
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                connection.commit()
        finally:
            ddl_engine.dispose()
        seed_database(self.engine, "synthetic-T17L-fixture-not-a-login-credential")

    def create(self, database):
        if database not in {self.database, self.database + "_restore"}:
            raise RuntimeError("Unexpected database")
        with self.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.exec_driver_sql(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )

    def drop(self, database):
        if database not in {self.database, self.database + "_restore"}:
            raise RuntimeError("Unexpected database")
        with self.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.exec_driver_sql(f"DROP DATABASE `{database}`")

    def archive(self):
        guard(self.engine, self.settings)
        expected = table_hashes(self.engine)
        statements = ["SET FOREIGN_KEY_CHECKS=0"]
        raw = self.engine.raw_connection()
        try:
            with raw.cursor() as cursor:
                for table in sorted(expected):
                    cursor.execute(f"SHOW CREATE TABLE `{table}`")
                    statements.append(cursor.fetchone()[1])
                    cursor.execute(f"SELECT * FROM `{table}`")
                    columns = [d[0] for d in cursor.description]
                    for row in cursor.fetchall():
                        names = ",".join("`" + c + "`" for c in columns)
                        placeholders = ",".join(["%s"] * len(columns))
                        sql = f"INSERT INTO `{table}` ({names}) VALUES ({placeholders})"
                        statements.append(cursor.mogrify(sql, row))
                cursor.execute("SHOW TRIGGERS")
                for trigger in cursor.fetchall():
                    cursor.execute(f"SHOW CREATE TRIGGER `{trigger[0]}`")
                    statements.append(cursor.fetchone()[2])
        finally:
            raw.close()
        # JSON statement boundaries preserve trigger bodies without fragile SQL splitting.
        archive = self.output / "database-statements.json"
        write_json(archive, statements + ["SET FOREIGN_KEY_CHECKS=1"])
        saved = json.loads(archive.read_text(encoding="utf-8"))
        self.create(self.database + "_restore")
        restored = create_database_engine(self.admin_settings, database=self.database + "_restore")
        guard(restored)
        try:
            with restored.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                for sql in saved:
                    c.exec_driver_sql(sql)
            actual = table_hashes(restored)
            if actual != expected:
                raise RuntimeError("SQL roundtrip mismatch: keep original and restore databases")
            write_json(
                self.output / "database-verification.json",
                {
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "tables": expected,
                    "roundtrip_matches": True,
                    "format": "JSON array of native MySQL statements",
                },
            )
        except Exception:
            restored.dispose()
            raise
        restored.dispose()
        self.drop(self.database + "_restore")
        self.engine.dispose()
        self.drop(self.database)
        if self.user is not None:
            with self.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.exec_driver_sql(f"DROP USER '{self.user}'@'localhost'")
        self.admin.dispose()
