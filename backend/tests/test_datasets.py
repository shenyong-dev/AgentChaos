"""Automated T13 infrastructure controls; these approvals are never real human reviews."""

import copy
import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.api.judging import get_judge_service
from app.core.config import Settings
from app.datasets import catalog
from app.datasets import service as dataset_service
from app.datasets.service import DatasetService
from app.db.seeds import seed_database
from app.executors.service import RunService
from app.judging.service import JudgeService
from app.main import create_app
from app.models import Artifact, Booking, TestCase, TestExecution, TestRun
from app.schemas.datasets import DatasetReview
from app.schemas.runs import CreateRun


def approve(value):
    return DatasetReview(
        request_id=uuid4(),
        package_hash=value["package_hash"],
        critical_cases=value["critical_cases"],
        reviewed_all_cases=True,
        reviewed_critical_expectations=True,
        approved_experiment_scale=True,
        note="AUTOMATED INFRASTRUCTURE CONTROL - NOT REAL HUMAN APPROVAL",
    )


def resign(value):
    for c in value["cases"]:
        c["case_hash"] = canonical_hash({k: v for k, v in c.items() if k != "case_hash"})
    value["package_hash"] = canonical_hash({k: v for k, v in value.items() if k != "package_hash"})
    return value


def test_catalog_coverage_independent_arithmetic_duplicates_and_family_leakage():
    value = catalog.load_catalog()
    stats = catalog.audit(value)
    assert (
        stats["splits"] == {"dev": 60, "calibration": 30, "holdout": 30} and stats["critical"] == 20
    )
    mutated = copy.deepcopy(value)
    mutated["cases"][1]["dsl"]["family_key"] = mutated["cases"][15]["dsl"]["family_key"]
    with pytest.raises(ValueError, match="family crosses"):
        catalog.audit(resign(mutated))
    mutated = copy.deepcopy(value)
    mutated["cases"][0]["independent_expectation"]["final_booking"]["attendees"] = 21
    with pytest.raises(ValueError, match="successful booking"):
        catalog.audit(resign(mutated))
    mutated = copy.deepcopy(value)
    mutated["cases"][1]["dsl"]["turns"] = mutated["cases"][0]["dsl"]["turns"]
    with pytest.raises(ValueError, match="Duplicate normalized"):
        catalog.audit(resign(mutated))
    mutated = copy.deepcopy(value)
    mutated["cases"][0]["title"] = "tampered"
    with pytest.raises(ValueError, match="Package hash"):
        catalog.audit(mutated)


def test_plan_counts_cost_and_no_fake_v1_or_future_authorization():
    value = catalog.load_catalog()
    p = catalog.experiment_plan(value)
    episodes = p["E2"]["episodes"]
    assert len(episodes) == 320 and p["E1"]["judge_calls"] == 60
    assert p["role_caps_microusd"] == {"agent": 54_300_000, "case_generator": 0, "judge": 1_350_000}
    assert p["total_planning_cap_microusd"] == 55_650_000 and p["paid_calls_this_task"] == 0
    for case in value["cases"]:
        jobs = [j for j in episodes if j["case_key"] == case["id"]]
        assert len(jobs) == (6 if case["critical"] else 2)
        assert [j["seed"] for j in jobs if j["version"] == "V0"] == [
            j["seed"] for j in jobs if j["version"] == "V1"
        ]


@pytest.mark.skipif(
    os.environ.get("AC_INTEGRATION") != "1", reason="explicit disposable MySQL integration"
)
def test_revision_reuses_117_and_preserves_original_120(monkeypatch):
    current = catalog.load_catalog()
    original = json.loads((catalog.PROJECT_ROOT / "datasets/v1/candidate.json").read_text("utf-8"))
    with disposable_database("T13") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        svc = DatasetService(engine, Settings())
        monkeypatch.setattr(dataset_service, "load_catalog", lambda: original)
        first = svc.review(2, approve(original))
        with Session(engine) as session:
            before = {r.id: r.content_hash for r in session.scalars(select(TestCase))}
            artifact = svc._row(session, "freeze")
            artifact_id = artifact.id
            frozen_bytes = (catalog.PROJECT_ROOT / artifact.relative_path).read_bytes()
        monkeypatch.setattr(dataset_service, "load_catalog", catalog.load_catalog)
        second = svc.review(2, approve(current))
        replaced = {"T13-M022", "T13-M023", "T13-S014"}
        for key, row in second["cases"].items():
            assert (row["id"] != first["cases"][key]["id"]) == (key in replaced)
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(TestCase)) == 123
            assert {i: session.get(TestCase, i).content_hash for i in before} == before
            old_artifact = session.get(Artifact, artifact_id)
            assert (catalog.PROJECT_ROOT / old_artifact.relative_path).read_bytes() == frozen_bytes
            assert svc._read(old_artifact) == first
            assert svc._row(session, "freeze").id != artifact_id
            for key in replaced:
                old = session.get(TestCase, first["cases"][key]["id"])
                new = session.get(TestCase, second["cases"][key]["id"])
                assert old.revision == 1 and new.revision == 2
            for model in (TestRun, TestExecution, Booking):
                assert session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.skipif(
    os.environ.get("AC_INTEGRATION") != "1", reason="explicit disposable MySQL integration"
)
def test_approval_access_freeze_binding_cold_requests_and_tamper_fail_closed(tmp_path, monkeypatch):
    value = catalog.load_catalog()
    s = Settings()
    assert not any(
        (
            s.allow_paid_model_calls,
            s.executor_allow_paid_model_calls,
            s.casegen_allow_paid_model_calls,
            s.judge_allow_paid_model_calls,
        )
    )
    with disposable_database("T13") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        svc = DatasetService(engine, s)
        request = approve(value)
        with pytest.raises(AgentError):
            svc.review(1, request)
        with pytest.raises(AgentError):
            svc.requests(1)
        frozen = svc.review(2, request)
        assert svc.review(2, request) == frozen
        with pytest.raises(AgentError):
            svc.review(2, approve(value))
        assert len(frozen["cases"]) == 120
        bound = svc.bind(1)
        assert bound["V1"] is None and len(bound["V0"]) == 12
        drafts = svc.requests(1)["requests"]
        assert len(drafts) == 160
        for job in drafts:
            c = next(c for c in value["cases"] if c["id"] == job["plan_case"])
            assert bool(job["request"]["fault_profile_id"]) == bool(c["fault"])
            assert job["request"]["repetitions"] == 1
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(TestCase)) == 120
            for model in (TestRun, TestExecution, Booking):
                assert session.scalar(select(func.count()).select_from(model)) == 0
        # Real SQL cold preparation for every frozen knowledge profile; no dispatch/model.
        prepared = set()
        for job in drafts:
            c = next(c for c in value["cases"] if c["id"] == job["plan_case"])
            profile = c["knowledge_profile"]
            if profile in prepared:
                continue
            runs = RunService(engine, s, document_ids=frozen["knowledge_profiles"][profile])
            identifier = runs.create(1, CreateRun.model_validate(job["request"]))
            assert runs.read(1, identifier)["status"] == "queued"
            runs.cancel(1, identifier)
            prepared.add(profile)
        assert len(prepared) == 12
        app = create_app()
        app.dependency_overrides[get_judge_service] = lambda: JudgeService(engine, None, s)
        with TestClient(app) as client:
            assert client.get("/api/v1/datasets/current").status_code == 401
            assert (
                client.get(
                    "/api/v1/datasets/current/cases/T13-N016", auth=("demo_operator", TEST_PASSWORD)
                ).status_code
                == 404
            )
            assert (
                client.get(
                    "/api/v1/datasets/current/cases/T13-N016", auth=("demo_reviewer", TEST_PASSWORD)
                ).status_code
                == 200
            )
            assert (
                client.post(
                    "/api/v1/datasets/current/review",
                    auth=("demo_operator", TEST_PASSWORD),
                    json=request.model_dump(mode="json"),
                ).status_code
                == 403
            )
        with pytest.raises(RuntimeError):
            migrate(engine, "downgrade", "20261028_0005")
        altered = copy.deepcopy(value)
        altered["cases"][0]["title"] = "changed after freeze"
        resign(altered)
        p = tmp_path / "altered.json"
        p.write_text(json.dumps(altered), encoding="utf-8")
        (tmp_path / "knowledge.json").write_text(json.dumps(value["knowledge"]), encoding="utf-8")
        monkeypatch.setattr(catalog, "PATH", p)
        with pytest.raises(AgentError) as e:
            svc.summary(1)
        assert e.value.code in {"dataset_changed_after_freeze", "dataset_invalid"}
        monkeypatch.undo()
        with Session(engine) as session:
            a = session.scalar(select(Artifact).where(Artifact.kind == "dataset_manifest"))
            original = catalog.PROJECT_ROOT / a.relative_path
            original.write_text("tamper", encoding="utf-8")
        with pytest.raises(AgentError) as e:
            svc.summary(1)
        assert e.value.code == "dataset_evidence_invalid"
