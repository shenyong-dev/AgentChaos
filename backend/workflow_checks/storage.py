"""Never remove a verification database unless its owned export is verified."""

import hashlib
import json
import zipfile
from uuid import uuid4

from sqlalchemy import text

from app.core.config import Settings
from app.db.engine import create_database_engine

TABLES = (
    "ac_target_versions",
    "ac_test_cases",
    "ac_test_runs",
    "ac_test_executions",
    "ac_trace_events",
    "ac_assertion_results",
    "ac_artifacts",
    "sb_bookings",
    "sb_booking_drafts",
    "sb_booking_slots",
)


def export(engine, destination, *, files=None):
    """One RR snapshot, one archive; no user password hashes or connection strings."""
    assert not destination.exists(), "Never overwrite an earlier export"
    records = {}
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            for name in TABLES:
                records[name] = [
                    dict(row)
                    for row in connection.execute(text(f"SELECT * FROM `{name}`")).mappings()
                ]
    manifest = {}
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, rows in records.items():
            data = (
                json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str) + "\n"
            ).encode()
            archive.writestr("tables/" + name + ".json", data)
            manifest[name] = {"rows": len(rows), "sha256": hashlib.sha256(data).hexdigest()}
        if files and files.exists():
            for path in sorted(files.rglob("*")):
                if path.is_file():
                    archive.write(path, "worker-files/" + path.relative_to(files).as_posix())
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        for name, record in manifest.items():
            data = archive.read("tables/" + name + ".json")
            assert hashlib.sha256(data).hexdigest() == record["sha256"]
            assert len(json.loads(data)) == record["rows"]
    return {
        "tables": manifest,
        "archive_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "verified": True,
    }


class OwnedDatabase:
    def __init__(self, local):
        self.local = local
        self.base = Settings()
        self.database = "agentchaos_t17h_verify_" + uuid4().hex[:12]
        self.admin = create_database_engine(self.base, database="mysql")
        self.engine = None
        self.export_verified = False

    def __enter__(self):
        assert self.database != self.base.mysql_database
        with self.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE `{self.database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
        self.engine = create_database_engine(self.base, database=self.database)
        return self.engine

    def __exit__(self, exc_type, *_):
        removed = False
        try:
            if self.engine:
                self.engine.dispose()
            if self.export_verified and exc_type is None:
                with self.admin.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as connection:
                    connection.execute(text(f"DROP DATABASE `{self.database}`"))
                removed = True
            (self.local / "storage-cleanup.json").write_bytes(
                (
                    json.dumps(
                        {
                            "owned_database": self.database,
                            "export_verified": self.export_verified,
                            "removed": removed,
                            "boundary": "On export/cleanup failure preserve owned SQL "
                            "for investigation; never clean main database.",
                        },
                        indent=2,
                    )
                    + "\n"
                ).encode()
            )
        finally:
            self.admin.dispose()
