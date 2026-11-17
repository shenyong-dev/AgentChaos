"""The workbench filters the displayed result before applying the page limit."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate
from test_executor import add_case

from app.agents.versions import register_version
from app.core.config import Settings
from app.db.seeds import seed_database
from app.executors.service import RunService
from app.models import TestExecution, TestRun, User
from app.schemas.runs import CreateRun
from app.workbench.service import WorkbenchService


@pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="Disposable MySQL schema")
def test_displayed_results_filter_before_pagination_and_preserve_lifecycle():
    with disposable_database("T17C") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        settings = Settings()
        runs = RunService(engine, settings)
        version = register_version(engine, runs.current_manifest())
        case_id = add_case(engine)
        with Session(engine) as db, db.begin():
            owner = db.get(User, 1)
            other = User(
                username="other_operator", password_hash=owner.password_hash, role="operator"
            )
            db.add(other)

        observed = {}
        for key, status, execution_statuses in [
            ("old_pass", "completed", ["passed"]),
            ("mixed", "completed", ["passed", "failed"]),
            ("error", "completed", ["inconclusive", "failed", "error"]),
            ("uncertain", "completed", ["passed", "inconclusive"]),
            ("ended", "completed", ["passed", "skipped"]),
            ("new_pass", "completed", ["passed", "passed"]),
            ("run_failure", "failed", ["error"]),
            ("queued", "queued", ["queued"]),
        ]:
            identifier = runs.create(
                1,
                CreateRun(
                    request_id=uuid4(),
                    target_version_id=version,
                    case_revision_ids=[case_id],
                    repetitions=len(execution_statuses),
                    budgets={"agent_microusd": 0},
                ),
            )
            observed[key] = str(identifier)
            with Session(engine) as db, db.begin():
                run = db.scalar(select(TestRun).where(TestRun.run_uuid == str(identifier)))
                rows = list(
                    db.scalars(
                        select(TestExecution)
                        .where(TestExecution.run_id == run.id)
                        .order_by(TestExecution.repeat_no)
                    )
                )
                for row, execution_status in zip(rows, execution_statuses, strict=True):
                    row.status = execution_status
                run.status = status

        workbench = WorkbenchService(engine, settings)
        first = workbench.runs(1, result="passed", limit=1)
        assert first["has_more"] and first["items"][0]["run_id"] == observed["new_pass"]
        second = workbench.runs(1, result="passed", limit=1, cursor=first["next_cursor"])
        assert not second["has_more"] and second["items"][0]["run_id"] == observed["old_pass"]
        for result, key in {
            "has_failed": "mixed",
            "error": "error",
            "inconclusive": "uncertain",
            "ended": "ended",
        }.items():
            (row,) = workbench.runs(1, result=result)["items"]
            assert (row["run_id"], row["status"], row["display_status"]) == (
                observed[key],
                "completed",
                result,
            )
        (failed,) = workbench.runs(1, status="failed")["items"]
        assert failed["run_id"] == observed["run_failure"] and failed["display_status"] == "failed"
        assert len(workbench.runs(1, status="completed")["items"]) == 6
        assert (
            workbench.runs(1, query=observed["old_pass"][:8], result="passed")["items"][0]["run_id"]
            == observed["old_pass"]
        )
        with Session(engine) as db:
            other_id = db.scalar(select(User.id).where(User.username == "other_operator"))
        assert workbench.runs(other_id, result="passed")["items"] == []
