"""Real SQL discovery/control checks with no model or broker dispatch."""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate
from test_executor import add_case

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash, register_version
from app.api.booking import get_booking_service
from app.api.cases import get_case_service
from app.api.runs import get_run_service
from app.api.workbench import get_workbench_service
from app.cases.service import CaseService
from app.core.config import Settings
from app.db.seeds import seed_database
from app.executors.service import RunService, case_content
from app.main import create_app
from app.models import Booking, TestCase, TestExecution, TestRun, User
from app.schemas.runs import CreateRun
from app.services.booking import BookingService
from app.workbench.service import WorkbenchService

pytestmark = pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="disposable MySQL")


def test_discovery_pages_versions_and_no_model_side_effects():
    s = Settings()
    with disposable_database("T14") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        ids = [add_case(engine) for _ in range(23)]
        legacy = add_case(engine, approved=False)
        with Session(engine) as session, session.begin():
            reviewed_at = session.get(TestCase, ids[0]).reviewed_at
            row = session.get(TestCase, legacy)
            row.fixture_json = {**row.fixture_json, "dataset": {"knowledge_profile": "legacy"}}
            row.review_status = "approved"
            row.reviewer_id = 2
            row.reviewed_at = reviewed_at
            row.content_hash = canonical_hash(case_content(row))
        ids.append(legacy)
        draft = add_case(engine, approved=False)
        holdout = add_case(engine, approved=False)
        with Session(engine) as session, session.begin():
            reviewed_at = session.get(TestCase, ids[0]).reviewed_at
            row = session.get(TestCase, holdout)
            row.dataset_split = "holdout"
            row.family_key = "independent-holdout-access-control-fixture"
            row.review_status = "approved"
            row.reviewer_id = 2
            row.reviewed_at = reviewed_at
            row.content_hash = canonical_hash(case_content(row))
        svc = WorkbenchService(engine, s)
        first = svc.cases(1, limit=20)
        second = svc.cases(1, cursor=first["next_cursor"], limit=20)
        assert len(first["items"]) == 20 and len(second["items"]) == 4
        legacy_row = next(r for r in second["items"] if r["id"] == legacy)
        assert legacy_row["knowledge_profile"] == "legacy"
        assert not legacy_row["default_knowledge_compatible"]
        assert first["items"][0]["default_knowledge_compatible"]
        assert {r["id"] for r in first["items"] + second["items"]} == set(ids)
        assert not second["has_more"] and svc.overview(1)["counts"]["runs"] == 0
        with pytest.raises(AgentError):
            svc.case(1, draft)
        with pytest.raises(AgentError):
            svc.case(1, holdout)
        with pytest.raises(AgentError):
            svc.overview(2)
        manifest = RunService(engine, s).current_manifest()
        current = register_version(engine, manifest)
        historical = {**manifest, "model_requested": "historical-not-current"}
        historical["config_hash"] = canonical_hash(
            {k: v for k, v in historical.items() if k != "config_hash"}
        )
        old = register_version(engine, historical)
        versions = svc.versions(1)["items"]
        assert next(v for v in versions if v["id"] == current)["compatible"]
        assert not next(v for v in versions if v["id"] == old)["compatible"]
        with Session(engine) as session:
            for model in (TestRun, TestExecution, Booking):
                assert session.scalar(select(func.count()).select_from(model)) == 0


def test_owned_run_filter_and_http_auth_bounds():
    s = Settings()
    with disposable_database("T14") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        case = add_case(engine)
        runs = RunService(engine, s)
        svc = WorkbenchService(engine, s)
        version = register_version(engine, runs.current_manifest())
        identifiers = [
            runs.create(
                1,
                CreateRun(
                    request_id=uuid4(),
                    target_version_id=version,
                    case_revision_ids=[case],
                    budgets={"agent_microusd": 0},
                ),
            )
            for _ in range(3)
        ]
        runs.cancel(1, identifiers[0])
        assert len(svc.runs(1, status="cancelled")["items"]) == 1
        assert svc.runs(1, query=identifiers[1][:8])["items"][0]["run_id"] == identifiers[1]
        with Session(engine) as session, session.begin():
            owner = session.get(User, 1)
            other = User(
                username="workbench_other", password_hash=owner.password_hash, role="operator"
            )
            session.add(other)
            session.flush()
            other_id = other.id
        assert not svc.runs(other_id)["items"] and not svc.versions(other_id)["items"]
        app = create_app()
        app.dependency_overrides[get_workbench_service] = lambda: svc
        app.dependency_overrides[get_case_service] = lambda: CaseService(engine, None, s)
        app.dependency_overrides[get_booking_service] = lambda: BookingService(engine)
        app.dependency_overrides[get_run_service] = lambda: runs
        with TestClient(app) as client:
            assert client.get("/api/v1/workbench/runs").status_code == 401
            auth = ("demo_operator", TEST_PASSWORD)
            assert client.get("/api/v1/workbench/runs?limit=51", auth=auth).status_code == 400
            assert (
                client.get("/api/v1/workbench/runs?status=invented", auth=auth).status_code == 400
            )
            assert (
                client.get(
                    "/api/v1/workbench/overview", auth=("demo_reviewer", TEST_PASSWORD)
                ).status_code
                == 403
            )
            assert (
                client.get("/api/v1/workbench/cases", auth=auth).json()["data"]["items"][0]["id"]
                == case
            )
            assert (
                client.get(
                    "/api/v1/runs/" + identifiers[1], auth=("workbench_other", TEST_PASSWORD)
                ).status_code
                == 404
            )
