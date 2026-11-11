"""Imported development access must not imply a new formal dataset approval."""

import hashlib
import json
import os
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate
from test_executor import add_case

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.seeds import seed_database
from app.executors.service import case_content
from app.models import Artifact, TestCase
from app.workbench.service import WorkbenchService

pytestmark = pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="disposable MySQL")


def test_import_is_hash_bound_cloud_only_and_cannot_expose_holdout_or_drafts(monkeypatch):
    deployment = uuid4().hex
    settings = Settings(cloud_deployment_id=deployment)
    path = PROJECT_ROOT / ".local/cloud" / ("test-import-" + deployment + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with disposable_database("T17E") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        ids = [add_case(engine, approved=False) for _ in range(4)]
        with Session(engine) as db, db.begin():
            from datetime import UTC, datetime

            entries = []
            for index, identifier in enumerate(ids):
                row = db.get(TestCase, identifier)
                row.case_key = "T13-CLOUD-" + str(index)
                row.family_key = "cloud-import-isolated-" + str(index)
                row.dataset_split = "holdout" if index == 2 else "dev"
                if index != 3:
                    row.review_status, row.reviewer_id, row.reviewed_at = (
                        "approved",
                        2,
                        datetime.now(UTC),
                    )
                row.content_hash = canonical_hash(case_content(row))
                entries.append(
                    {"id": identifier, "content_hash": row.content_hash if index != 1 else "0" * 64}
                )
            raw = json.dumps(
                {
                    "deployment_id": deployment,
                    "kind": "imported_approved_development_revisions",
                    "cases": entries,
                }
            ).encode()
            path.write_bytes(raw)
            db.add(
                Artifact(
                    owner_id=1,
                    run_id=None,
                    kind="dataset_manifest",
                    relative_path=path.relative_to(PROJECT_ROOT).as_posix(),
                    sha256=hashlib.sha256(raw).hexdigest(),
                    size_bytes=len(raw),
                    generated_by_revision="cloud-import-control",
                    metadata_json={
                        "deployment_id": deployment,
                        "phase": "cloud_development_import",
                    },
                )
            )
        try:
            monkeypatch.setattr("app.core.cloud.team_cloud", lambda _: True)
            service = WorkbenchService(engine, settings)

            def visible():
                with Session(engine) as db:
                    return list(db.scalars(service._cases(db).with_only_columns(TestCase.id)))

            assert visible() == [ids[0]]
            path.write_bytes(raw + b" ")
            assert visible() == []
            path.write_bytes(raw)
            monkeypatch.setattr("app.core.cloud.team_cloud", lambda _: False)
            assert visible() == []
        finally:
            path.unlink(missing_ok=True)
