"""Owned, append-only registration on disposable MySQL; no model or seed side effects."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.api.cases import get_case_service
from app.api.workbench import get_version_registration_service
from app.cases.service import CaseService
from app.core.config import Settings
from app.db.seeds import seed_database
from app.main import create_app
from app.models import Target, TargetVersion, TestCase, TestRun, User
from app.schemas.versions import RegisterVersion
from app.workbench.versions import VersionRegistrationService

pytestmark = pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="disposable MySQL")


@pytest.fixture(scope="module")
def lab():
    with disposable_database("T14") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        settings = Settings().model_copy(
            update={
                "mysql_database": engine.url.database,
                "deepseek_api_key": SecretStr("explicit-test-secret-only"),
                "allow_paid_model_calls": False,
                "executor_allow_paid_model_calls": False,
                "casegen_allow_paid_model_calls": False,
                "judge_allow_paid_model_calls": False,
            }
        )
        with Session(engine) as session, session.begin():
            user = User(
                username="version_other",
                password_hash=session.get(User, 1).password_hash,
                role="operator",
            )
            session.add(user)
            session.flush()
            other = user.id
        yield engine, settings, other


@pytest.fixture
def setup(lab, monkeypatch):
    engine, settings, other = lab
    with Session(engine) as session, session.begin():
        rows = [
            Target(name="explicit version test " + uuid4().hex, adapter_type=adapter, owner_id=1)
            for adapter in ("langgraph_booking", "reference_refund")
        ]
        session.add_all(rows)
        session.flush()
        ids = [row.id for row in rows]
    service = VersionRegistrationService(engine, settings)
    value = {
        "baseline": "V1",
        "prompt_hash": "a" * 64,
        "retrieval": {"documents": []},
        "sources": {"old.py": "1" * 64, "shared.py": "2" * 64},
        "model_requested": "test-model",
    }
    monkeypatch.setattr(service, "current", lambda _: fingerprint(value))
    return engine, service, ids, value, other


def fingerprint(value):
    value = deepcopy(value)
    value.pop("config_hash", None)
    return {**value, "config_hash": canonical_hash(value)}


def request(service, target):
    result = service.inspect(1, target)
    return RegisterVersion(
        target_id=target,
        expected_config_hash=result["current_config_hash"],
        base_version_id=result["base_version"]["id"] if result["base_version"] else None,
        confirmed=True,
    )


def count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_read_only_diff_redacts_and_preserves_old_snapshot(setup):
    engine, service, ids, value, _ = setup
    first = service.register(1, request(service, ids[0]))
    before_count = count(engine, TargetVersion)
    with Session(engine) as session:
        original = deepcopy(session.get(TargetVersion, first["id"]).model_config_json)
    value["sources"] = {"shared.py": "3" * 64, "new.py": "4" * 64}
    value["model_requested"] = "second-model"
    value["tools"] = [{"api_key": "explicit-test-secret-only", "help": "explicit-test-secret-only"}]
    value["unknown_private"] = "never-display-this-raw-value"
    checked = service.inspect(1, ids[0])
    assert count(engine, TargetVersion) == before_count
    assert checked["registered_version"] is None and checked["base_version"]["id"] == first["id"]
    assert {f["path"]: f["status"] for f in checked["source_changes"]} == {
        "old.py": "removed",
        "shared.py": "modified",
        "new.py": "added",
    }
    serialized = json.dumps(checked)
    assert "explicit-test-secret-only" not in serialized
    assert "never-display-this-raw-value" not in serialized and "[REDACTED]" in serialized
    assert (
        next(c for c in checked["changes"] if c["key"] == "model_requested")["before"]
        == "test-model"
    )
    with Session(engine) as session:
        assert session.get(TargetVersion, first["id"]).model_config_json == original


@pytest.mark.parametrize("index", [0, 1])
def test_first_then_changed_registration_no_seeds_or_runs(setup, index):
    engine, service, ids, value, _ = setup
    before = {m: count(engine, m) for m in (Target, TargetVersion, TestCase, TestRun)}
    first_request = request(service, ids[index])
    first = service.register(1, first_request)
    assert first["created"] and first["version_no"] == 1
    assert service.register(1, first_request) == {**first, "created": False}
    value["model_requested"] = "changed-model"
    second = service.register(1, request(service, ids[index]))
    assert second["created"] and second["version_no"] == 2 and first["id"] != second["id"]
    with Session(engine) as session:
        assert (
            session.get(TargetVersion, first["id"]).model_config_json["model_requested"]
            == "test-model"
        )
        assert session.get(Target, ids[index]).capabilities_json == {}
    assert count(engine, TargetVersion) == before[TargetVersion] + 2
    for model in (Target, TestCase, TestRun):
        assert count(engine, model) == before[model]


def test_source_and_baseline_change_reject_without_writes(setup, monkeypatch):
    engine, service, ids, value, _ = setup
    old = request(service, ids[0])
    value["model_requested"] = "changed"
    with pytest.raises(AgentError, match="源码或配置"):
        service.register(1, old)
    fresh = request(service, ids[0])
    service.register(1, fresh)
    value["model_requested"] = "changed-again"
    stale = fresh.model_copy(update={"expected_config_hash": fingerprint(value)["config_hash"]})
    before = count(engine, TargetVersion)
    with pytest.raises(AgentError, match="新的登记"):
        service.register(1, stale)
    current_request = request(service, ids[0])
    first, second = fingerprint(value), fingerprint({**value, "model_requested": "during-register"})
    responses = iter([first, second])
    monkeypatch.setattr(service, "current", lambda _: next(responses))
    with pytest.raises(AgentError, match="核对期间"):
        service.register(1, current_request)
    assert count(engine, TargetVersion) == before


def test_duplicate_concurrent_request_reuses_one_append(setup):
    engine, service, ids, _, _ = setup
    payload = request(service, ids[0])
    before = count(engine, TargetVersion)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: service.register(1, payload), range(2)))
    assert {r["id"] for r in results} == {results[0]["id"]}
    assert sorted(r["created"] for r in results) == [False, True]
    assert count(engine, TargetVersion) == before + 1


def test_owner_role_archival_and_unsupported_bounds(setup):
    engine, service, ids, _, other = setup
    payload = request(service, ids[0])
    for action in (
        lambda: service.inspect(other, ids[0]),
        lambda: service.register(other, payload),
        lambda: service.inspect(2, ids[0]),
        lambda: service.targets(2),
    ):
        with pytest.raises(AgentError):
            action()
    assert not service.targets(other)["items"]
    with Session(engine) as session, session.begin():
        session.get(Target, ids[0]).archived_at = datetime.now(UTC)
        session.get(Target, ids[1]).adapter_type = "unsupported-test"
    with pytest.raises(AgentError):
        service.register(1, payload)
    with pytest.raises(AgentError):
        service.inspect(1, ids[1])
    listed = {t["id"] for t in service.targets(1)["items"]}
    assert not set(ids) & listed


def test_real_manifests_both_adapters_match_frozen_hash(lab):
    engine, settings, _ = lab
    service = VersionRegistrationService(engine, settings)
    before = count(engine, TargetVersion)
    manifests = [service.current(adapter) for adapter in ("langgraph_booking", "reference_refund")]
    assert all(fingerprint(m)["config_hash"] == m["config_hash"] for m in manifests)
    assert manifests[0]["config_hash"] != manifests[1]["config_hash"]
    assert manifests[1]["retrieval"]["mode"] == "not_applicable"
    assert count(engine, TargetVersion) == before


def test_http_confirmation_auth_and_repeat_receipt(setup):
    engine, service, ids, _, other = setup
    app = create_app()
    app.dependency_overrides[get_version_registration_service] = lambda: service
    app.dependency_overrides[get_case_service] = lambda: CaseService(engine, None, service.settings)
    path = "/api/v1/workbench/version-registration"
    auth = ("demo_operator", TEST_PASSWORD)
    with TestClient(app) as client:
        assert client.get(path + "/targets").status_code == 401
        assert (
            client.get(path + "/targets", auth=("demo_reviewer", TEST_PASSWORD)).status_code == 403
        )
        assert (
            client.get(
                path + f"/check?target_id={ids[0]}", auth=("version_other", TEST_PASSWORD)
            ).status_code
            == 404
        )
        assert client.get(path + "/check?target_id=0", auth=auth).status_code == 400
        checked = client.get(path + f"/check?target_id={ids[0]}", auth=auth).json()["data"]
        payload = {
            "target_id": ids[0],
            "expected_config_hash": checked["current_config_hash"],
            "base_version_id": None,
        }
        before = count(engine, TargetVersion)
        assert client.post(path, json=payload, auth=auth).status_code == 400
        assert (
            client.post(path, json={**payload, "confirmed": "true"}, auth=auth).status_code == 400
        )
        assert count(engine, TargetVersion) == before
        payload["confirmed"] = True
        receipt = client.post(path, json=payload, auth=auth)
        assert receipt.status_code == 200 and receipt.json()["data"]["created"]
        repeat = client.post(path, json=payload, auth=auth).json()["data"]
        assert not repeat["created"] and repeat["id"] == receipt.json()["data"]["id"]
        assert count(engine, TargetVersion) == before + 1
    for bad in ({"confirmed": False}, {"target_id": True}, {"unexpected": True}):
        with pytest.raises(ValidationError):
            RegisterVersion(**{**payload, **bad})
