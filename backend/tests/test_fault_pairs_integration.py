"""HTTP ownership and malformed comparison guards, no paid requests."""
# ruff: noqa: F811

from copy import deepcopy

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD
from test_reports import completed, lab, pytestmark  # noqa: F401

from app.api.cases import get_case_service
from app.api.reports import get_report_service
from app.cases.service import CaseService
from app.main import create_app
from app.models import AssertionResult, TestRun
from app.reports.analysis import canonical_hash
from app.reports.fault_pairs import compare_faults
from app.reports.service import ReportService


def test_http_fault_compare_is_owned_read_only_and_rejects_normal_normal(lab, completed):
    engine, redis, settings, *_ = lab
    service = ReportService(engine, settings)
    app = create_app()
    app.dependency_overrides[get_case_service] = lambda: CaseService(engine, redis, settings)
    app.dependency_overrides[get_report_service] = lambda: service

    def counts():
        with Session(engine) as session:
            return [
                session.scalar(select(func.count()).select_from(t))
                for t in (TestRun, AssertionResult)
            ]

    before = counts()
    with TestClient(app) as client:
        url = f"/api/v1/reports/fault-compare?normal={completed[0]}&fault={completed[2]}"
        assert client.get(url).status_code == 401
        assert client.get(url, auth=("demo_reviewer", TEST_PASSWORD)).status_code == 403
        assert client.get(url, auth=("t15_other", TEST_PASSWORD)).status_code == 404
        response = client.get(url, auth=("demo_operator", TEST_PASSWORD))
        assert response.status_code == 200, response.text
        value = response.json()["data"]
        assert not value["conditions_match"] and value["read_only"]
        assert value["summary"]["complete"] == 0
    assert counts() == before


def test_empty_model_and_configuration_drift_are_excluded(lab, completed):
    service = ReportService(lab[0], lab[2])
    normal = service.read(1, completed[2])
    fault = deepcopy(normal)
    spec = {"fault_type": "F04"}
    fault["run"]["manifest"]["fault_profile"] = {
        "config": spec,
        "config_hash": canonical_hash(spec),
    }
    fault["run"]["manifest"]["target"]["model_requested"] = None
    value = compare_faults(normal, fault)
    assert not value["conditions_match"] and value["summary"]["complete"] == 0
    assert next(c for c in value["checks"] if c["condition"] == "model")["status"] == "missing"
    fault["run"]["manifest"]["target"]["model_requested"] = "changed-model"
    assert (
        next(c for c in compare_faults(normal, fault)["checks"] if c["condition"] == "model")[
            "status"
        ]
        == "mismatch"
    )
