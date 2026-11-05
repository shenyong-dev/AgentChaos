"""A real failed export must keep its owned SQL; verified export permits cleanup."""

import json
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from workflow_checks import storage as store

pytestmark = pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="owned MySQL")


def test_failed_export_keeps_owned_database_and_successful_export_is_verified(
    tmp_path, monkeypatch
):
    from test_database import migrate

    owned = store.OwnedDatabase(tmp_path)
    original_tables = store.TABLES
    try:
        with pytest.raises(DBAPIError):
            with owned as engine:
                migrate(engine)
                monkeypatch.setattr(store, "TABLES", ("controlled_missing_table",))
                store.export(engine, tmp_path / "failed.zip")
        record = json.loads((tmp_path / "storage-cleanup.json").read_bytes())
        assert not record["removed"] and not record["export_verified"]
        with owned.admin.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=:name"
                    ),
                    {"name": owned.database},
                )
                == 1
            )
        monkeypatch.setattr(store, "TABLES", original_tables)
        proof = store.export(owned.engine, tmp_path / "verified.zip")
        assert proof["verified"] and set(proof["tables"]) == set(original_tables)
        with pytest.raises(AssertionError, match="overwrite"):
            store.export(owned.engine, tmp_path / "verified.zip")
        owned.export_verified = True
    finally:
        if owned.export_verified:
            owned.__exit__(None)
    assert json.loads((tmp_path / "storage-cleanup.json").read_bytes())["removed"]
