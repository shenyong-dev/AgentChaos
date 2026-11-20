"""Personal onboarding guards and the shared scheduler path."""
# ruff: noqa: F811

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD
from test_external_agents import TOKEN, remote  # noqa: F401
from test_reports import lab  # noqa: F401

from app.agents.errors import AgentError
from app.api.cases import get_case_service
from app.api.external_agents import get_external_service
from app.cases.service import CaseService
from app.core.config import Settings
from app.executors.dispatch import dispatch_pending
from app.executors.runner import run_execution
from app.executors.service import RunService
from app.external import self_service as personal
from app.external.cases import CaseFile
from app.external.registry import credential, entry_for
from app.external.service import ExternalService
from app.external.transport import Client
from app.main import create_app
from app.models import TestExecution, TestRun, User
from app.schemas.runs import CreateRun
from app.workbench.service import WorkbenchService

URL = "https://my-agent.example.org/agentchaos/v1"
PUBLIC_IP = "93.184.216.34"


@pytest.fixture
def agent_settings(tmp_path):
    return Settings(
        _env_file=None,
        app_env="test",
        external_agent_calls_enabled=True,
        external_agent_web_policy_path=str(tmp_path / "policy.json"),
        external_agent_self_service_key=Fernet.generate_key().decode(),
    )


@pytest.mark.parametrize(
    "address",
    [
        "http://my-agent.example.org/agentchaos/v1",
        "https://127.0.0.1/agentchaos/v1",
        "https://test.internal/agentchaos/v1",
        "https://my-agent.example.org:8443/agentchaos/v1",
        "https://my-agent.example.org/admin",
        "https://my-agent.example.org/../agentchaos/v1",
        "https://my-agent.example.org/agentchaos/v1?token=hidden",
        "https://example.org@my-agent.example.org/agentchaos/v1",
        "https://my-agent.example.org/agentchaos/v1#x",
    ],
)
def test_only_https_protocol_address(address):
    with pytest.raises(AgentError, match="公网 HTTPS"):
        personal.endpoint(address)


def dns_fixture(settings, monkeypatch, owner, addresses):
    queries = []
    proof = personal.challenge(settings, owner, URL)

    def resolve(name, kind, *, lifetime, search):
        assert lifetime == 3 and search is False
        queries.append((name, kind))
        if kind == "TXT":
            return [SimpleNamespace(strings=(proof["value"].encode(),))]
        return [SimpleNamespace(address=address) for address in addresses]

    monkeypatch.setattr(personal, "resolve", resolve)
    return queries


def test_dns_proof_is_bound_to_account_and_rejects_mixed_address_sets(agent_settings, monkeypatch):
    queries = dns_fixture(agent_settings, monkeypatch, 1, [PUBLIC_IP, "10.0.0.1"])
    with pytest.raises(AgentError) as wrong_account:
        personal.pin_ip(agent_settings, 2, URL)
    assert wrong_account.value.code == "external_dns_unverified"
    assert queries == [("_agentchaos.my-agent.example.org.", "TXT")]
    with pytest.raises(AgentError) as mixed:
        personal.pin_ip(agent_settings, 1, URL)
    assert mixed.value.code == "external_dns_address"
    assert personal.challenge(agent_settings, 1, URL) != personal.challenge(
        agent_settings, 1, "https://my-agent.example.org/team/agentchaos/v1"
    )


def test_saved_token_encrypted_and_owner_and_deployment_bound(
    agent_settings, monkeypatch, tmp_path
):
    dns_fixture(agent_settings, monkeypatch, 1, [PUBLIC_IP])
    monkeypatch.setattr(personal, "limited", lambda *_args: None)
    form = personal.Connect(endpoint=URL, name="Mine", revision="v1", access_token=TOKEN)
    record, exists = personal.candidate(agent_settings, 1, form)
    assert not exists
    personal.save(agent_settings, record)
    path = next(tmp_path.rglob("*.agent"))
    assert TOKEN.encode() not in path.read_bytes()
    found, exists = personal.candidate(agent_settings, 1, form)
    assert exists and found == record
    entry = entry_for(agent_settings, "external_http:" + record.entry.id, owner=1)
    assert credential(entry, agent_settings, owner=1) == TOKEN
    other_path = path.parent.parent / "2" / path.name
    other_path.parent.mkdir()
    other_path.write_bytes(path.read_bytes())
    with pytest.raises(AgentError) as mismatched_owner:
        credential(entry, agent_settings, owner=2)
    assert mismatched_owner.value.code == "external_self_store"
    mismatched = agent_settings.model_copy(update={"executor_namespace": "different"})
    assert personal.read(mismatched, 1, entry.id) is None
    with pytest.raises(AgentError):
        personal.candidate(agent_settings, 1, form.model_copy(update={"name": "Changed"}))


@pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="needs disposable MySQL/Redis")
def test_operator_self_onboards_and_uses_scheduler_without_reviewer(
    lab, remote, monkeypatch, tmp_path
):
    engine, redis, base, *_ = lab
    local_entry, local_settings, _ = remote
    settings = base.model_copy(
        update={
            "app_env": "test",
            "mysql_database": engine.url.database,
            "external_agent_calls_enabled": True,
            "external_agent_registry_path": "",
            "external_agent_web_policy_path": str(tmp_path / "policy.json"),
            "external_agent_self_service_key": agent_secret(),
        }
    )
    (tmp_path / "policy.json").write_text('{"profiles":[]}', encoding="utf-8")
    queries = dns_fixture(settings, monkeypatch, 1, [PUBLIC_IP])
    operations = []
    original = Client.call

    def network(self, body, *, deadline=None):
        assert self.owner == 1
        assert self.entry.endpoint == URL and self.entry.pinned_ip == PUBLIC_IP
        received = self._access_token or credential(self.entry, self.settings, owner=self.owner)
        assert received == TOKEN
        operations.append(body["operation"])
        return original(Client(local_entry, local_settings), body, deadline=deadline)

    monkeypatch.setattr(Client, "call", network)

    with Session(engine) as session:
        other = session.scalar(select(User.id).where(User.username == "t15_other"))
        reviewer = session.scalar(select(User.id).where(User.role == "reviewer"))
    svc = ExternalService(engine, settings)
    form = personal.Connect(
        endpoint=URL,
        name=local_entry.card.name,
        revision=local_entry.card.revision,
        access_token=TOKEN,
    )
    assert svc.definitions(1)["self_service_enabled"] is True
    with pytest.raises(AgentError):
        svc.connect(other, form)
    assert operations == []
    app = create_app()
    app.dependency_overrides[get_case_service] = lambda: CaseService(engine, redis, settings)
    app.dependency_overrides[get_external_service] = lambda: svc
    with TestClient(app) as api:
        endpoint = "/api/v1/external-agents/self-service"
        auth = ("demo_operator", TEST_PASSWORD)
        body = {**form.model_dump(exclude={"access_token"}), "access_token": TOKEN}
        assert api.post(endpoint + "/connect", json=body).status_code == 401
        challenge = api.post(endpoint + "/challenge", json={"endpoint": URL}, auth=auth)
        assert challenge.status_code == 200
        assert challenge.json()["data"]["name"] == "_agentchaos.my-agent.example.org"
        response = api.post(endpoint + "/connect", json=body, auth=auth)
        assert response.status_code == 200 and TOKEN not in response.text
        registered = response.json()["data"]
    assert registered["created"]
    assert not svc.connect(1, form)["created"]
    assert not svc.definitions(other)["items"]
    assert not svc.definitions(reviewer)["self_service_enabled"]
    with pytest.raises(AgentError):
        svc.inspect(other, registered["registered_id"])
    assert operations == ["capabilities"]
    assert [q[1] for q in queries] == ["TXT", "TXT", "A"]

    kind = "external_http:" + registered["registered_id"]
    dsl = CaseFile.model_validate(
        {
            "target_kind": kind,
            "case": {
                "case_key": "PERSONAL-" + uuid4().hex[:12].upper(),
                "revision": 1,
                "family_key": "personal-case",
                "category": "normal",
                "dataset_split": "dev",
                "reference_clock": "2026-11-19T10:00:00+08:00",
                "turns": [{"user": "你好"}, {"user": "请核对第二轮"}],
                "assertions": [{"type": "reply_literals", "contains": ["收到"], "excludes": []}],
            },
        }
    )
    with pytest.raises(AgentError):
        svc.import_case(other, dsl)
    case = svc.import_case(1, dsl)
    assert case["review_status"] == "draft" and case["runnable"]
    assert svc.cases(1)["items"][0]["id"] == case["id"]
    assert not svc.cases(reviewer)["items"] and not svc.cases(other)["items"]
    assert any(c["id"] == case["id"] for c in WorkbenchService(engine, settings).cases(1)["items"])
    assert not any(
        c["id"] == case["id"] for c in WorkbenchService(engine, settings).cases(other)["items"]
    )

    runs = RunService(engine, settings)
    args = CreateRun(
        request_id=uuid4(),
        target_version_id=registered["version_id"],
        case_revision_ids=[case["id"]],
    )
    with pytest.raises(AgentError):
        runs.create(other, args)
    run = runs.create(1, args)
    execution = runs.read(1, run)["executions"][0]["execution_id"]
    with Session(engine) as session:
        original_run = session.scalar(select(TestRun).where(TestRun.run_uuid == run))
        original_execution = session.scalar(
            select(TestExecution).where(TestExecution.execution_uuid == execution)
        )
        for owner, content_hash in [(other, case["content_hash"]), (1, "f" * 64)]:
            with (
                pytest.raises(OperationalError, match="approved_case_required"),
                session.begin_nested(),
            ):
                attempt = TestRun(
                    run_uuid=str(uuid4()),
                    created_by=owner,
                    target_version_id=original_run.target_version_id,
                    manifest_json=original_run.manifest_json,
                    manifest_hash=original_run.manifest_hash,
                    budget_json=original_run.budget_json,
                )
                session.add(attempt)
                session.flush()
                session.add(
                    TestExecution(
                        execution_uuid=str(uuid4()),
                        run_id=attempt.id,
                        case_revision_id=case["id"],
                        repeat_no=1,
                        seed=0,
                        scope_id="test:" + str(uuid4()),
                        session_id=str(uuid4()),
                        input_snapshot_json={
                            **original_execution.input_snapshot_json,
                            "content_hash": content_hash,
                        },
                    )
                )
                session.flush()
    assert runs.can_consume(execution)
    published = []
    assert dispatch_pending(engine, settings, publisher=published.append)["published"] == 1
    assert published == [execution]
    result = run_execution(engine, redis, settings, execution)
    assert result["status"] == "passed"
    assert operations == ["capabilities", "capabilities", "prepare", "execute", "execute"]
    assert TOKEN not in str(runs.read(1, run))


def agent_secret():
    from pydantic import SecretStr

    return SecretStr(Fernet.generate_key().decode())
