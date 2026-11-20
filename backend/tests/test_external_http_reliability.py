"""Mapped HTTP acceptance on isolated SQL/Redis and a separate loopback Agent API."""
# ruff: noqa: F811

import json
import os
from http.server import ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session
from test_reports import lab  # noqa: F401

from app.agents.errors import AgentError
from app.executors.runner import run_execution
from app.executors.service import RunService
from app.external import otel, self_service
from app.external.cases import CaseFile
from app.external.receipts import reconcile
from app.external.registry import entry_for
from app.external.service import ExternalService
from app.main import create_app
from app.models import User
from app.reports.service import ReportService
from app.schemas.reports import ExportReport
from app.schemas.runs import CreateRun
from examples.external_http.server import POLICY, Helpdesk, handler

pytestmark = pytest.mark.skipif(
    os.getenv("AC_INTEGRATION") != "1", reason="isolated MySQL and Redis"
)


def case_for(entry):
    key = "EXT-" + uuid4().hex[:12].upper()
    return CaseFile.model_validate(
        {
            "target_kind": f"external_http:{entry}",
            "case": {
                "schema_version": "external-reliability-1",
                "case_key": key,
                "revision": 1,
                "family_key": key,
                "category": "normal",
                "dataset_split": "dev",
                "reference_clock": "2026-11-20T12:00:00+08:00",
                "turns": [{"user": "查退货政策"}, {"user": "创建工单"}],
                "assertions": [
                    {"type": "reply_literals", "contains": ["工单已创建"]},
                    {
                        "type": "external_retrieval_top_k",
                        "k": 1,
                        "document_ids": [POLICY],
                        "turn": 1,
                    },
                    {
                        "type": "external_tool_sequence",
                        "steps": [
                            {"name": "lookup_policy", "arguments": {"policy_id": POLICY}},
                            {"name": "create_ticket", "arguments": {"category": "return"}},
                        ],
                    },
                    {"type": "external_fact_equals", "path": "write_count", "value": 1},
                ],
            },
        }
    )


@pytest.fixture
def mapped(lab, tmp_path, monkeypatch):
    engine, redis, original, *_ = lab
    registry = tmp_path / "registry.json"
    policy = tmp_path / "policy.json"
    policy.write_text('{"profiles":[]}', encoding="utf-8")
    settings = original.model_copy(
        update={
            "app_env": "test",
            "mysql_database": engine.url.database,
            "external_agent_calls_enabled": True,
            "external_agent_web_policy_path": str(policy),
            "external_agent_self_service_key": SecretStr(Fernet.generate_key().decode()),
            "external_agent_registry_path": str(registry),
        }
    )
    monkeypatch.setattr("app.api.external_agents.get_settings", lambda: settings)
    credential = uuid4().hex
    monkeypatch.setenv("AGENTCHAOS_EXTERNAL_HELPDESK", credential)
    export_enabled = {"value": True}
    with TestClient(create_app()) as client:

        def export(payload, dify):
            if not export_enabled["value"]:
                return
            token = otel.exporter_token(settings, 1, "dify-test" if dify else "mapped-test")
            response = client.post(
                "/api/v1/external-agents/otlp/v1/traces",
                headers={"Authorization": "Bearer " + token},
                json=payload,
            )
            assert response.status_code == 200, response.text

        store = Helpdesk(tmp_path / "tickets.sqlite")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(store, credential, export))
        port = server.server_address[1]
        common = {
            "pinned_ip": "127.0.0.1",
            "network_zone": "approved_private",
            "credential_env": "AGENTCHAOS_EXTERNAL_HELPDESK",
            "author_relation": "project_example",
            "enabled": True,
            "cost_upper_microusd": 2,
        }
        targets = [
            {
                **common,
                "endpoint": f"http://127.0.0.1:{port}/chat",
                "id": "mapped-test",
                "http": {
                    "session_field": "session_id",
                    "session_path": "session_id",
                    "retrieval_path": "metadata.documents",
                    "readback_path": "/facts",
                    "sandbox_tool_fault": True,
                },
                "card": {
                    "protocol": "http-json",
                    "name": "Standalone HTTP",
                    "revision": "v1",
                    "billing": "external_account",
                },
            },
            {
                **common,
                "endpoint": f"http://127.0.0.1:{port}/v1/chat-messages",
                "id": "dify-test",
                "http": {
                    "message_field": "query",
                    "session_field": "conversation_id",
                    "session_path": "conversation_id",
                    "retrieval_path": "metadata.retriever_resources",
                    "readback_path": "/facts",
                    "body_fields": {"inputs": {}, "response_mode": "streaming"},
                },
                "card": {
                    "protocol": "dify",
                    "name": "Standalone Dify shape",
                    "revision": "v1",
                    "billing": "external_account",
                },
            },
        ]
        registry.write_text(
            json.dumps({"schema_version": "agentchaos-external-registry-1", "targets": targets}),
            encoding="utf-8",
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield engine, redis, settings, store, export_enabled
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=4)


def run_case(engine, redis, settings, run_service, version, case_id, fault=None):
    identifier = run_service.create(
        1,
        CreateRun(
            request_id=uuid4(),
            target_version_id=version,
            case_revision_ids=[case_id],
            external_fault=fault,
        ),
    )
    execution = run_service.read(1, identifier)["executions"][0]["execution_id"]
    result = run_execution(engine, redis, settings, execution)
    return identifier, execution, result


def test_http_evidence_fault_pairs_and_readback_without_resending(mapped):
    engine, redis, settings, sandbox, _ = mapped
    service = ExternalService(engine, settings)
    inspection = service.inspect(1, "mapped-test")
    assert inspection["configuration_valid"] and not inspection["connected"]
    item = service.register(1, "mapped-test", inspection["config_hash"])
    draft = service.import_case(1, case_for("mapped-test"))
    service.review(2, draft["id"], draft["content_hash"], "approved")
    runs, reports = RunService(engine, settings), ReportService(engine, settings)
    outcomes = {}
    for fault in (None, "response_timeout", "tool_error"):
        run, execution, result = run_case(
            engine,
            redis,
            settings,
            runs,
            item["version_id"],
            draft["id"],
            fault,
        )
        assert result["status"] == ("inconclusive" if fault == "response_timeout" else "passed"), (
            result
        )
        report = reports.read(1, run)
        record = report["executions"][0]
        assert record["before"]["data"]["write_count"] == 0
        assert record["after"]["data"]["write_count"] == 1
        assert record["after"]["data"]["request_count"] == 1
        assert report["run"]["manifest"]["external_fault"] == fault
        evidence = record["output"]["external_evidence"]
        assert evidence["rounds"][0]["documents"] == [POLICY]
        assert evidence["rounds"][1]["trace"]["complete"]
        if fault == "tool_error":
            assert evidence["transport"]["tool_fault_observed"]
        if fault == "response_timeout":
            assert record["output"]["assistant_text"] is None
            assert report["run"]["summary"]["external"]["held_upper"] == 0
            receipt = reconcile(engine, settings, 1, run, execution)
            assert (
                receipt["inputs_resent"] == 0 and receipt["items"][0]["status"] == "fact_observed"
            )
            fact = receipt["items"][0]["fact"]["data"]
            assert sandbox.facts(fact["request_id"], fact["session_id"])["request_count"] == 1
            assert runs.read(1, run)["executions"][0]["status"] == "inconclusive"
        outcomes[fault] = run
    pair = reports.fault_comparison(1, outcomes[None], outcomes["response_timeout"])
    assert pair["conditions_match"] and pair["pairs"][0]["fault"]["transport"] == "receipt_timeout"
    assert len(reports.export(1, outcomes[None], ExportReport(request_id=uuid4()))["items"]) == 3


def test_dify_wire_session_and_missing_traces_produce_evidence_insufficient(mapped):
    engine, redis, settings, _, enabled = mapped
    enabled["value"] = False
    service = ExternalService(engine, settings)
    inspection = service.inspect(1, "dify-test")
    assert inspection["connected"]
    version = service.register(1, "dify-test", inspection["config_hash"])
    draft = service.import_case(1, case_for("dify-test"))
    service.review(2, draft["id"], draft["content_hash"], "approved")
    run, _, result = run_case(
        engine, redis, settings, RunService(engine, settings), version["version_id"], draft["id"]
    )
    assert result["status"] == "inconclusive", result
    checks = ReportService(engine, settings).read(1, run)["executions"][0]["assertions"]
    verdicts = {item["metric"]: item["verdict"] for item in checks}
    assert verdicts["external_retrieval_top_k"] == "passed"
    assert verdicts["external_tool_sequence"] == "inconclusive"
    assert verdicts["external_fact_equals"] == "passed"
    answers = RunService(engine, settings).read(1, run)["executions"][0]["output"]
    assert answers["assistant_text"].count("工单已创建") == 1


def test_ordinary_account_keeps_own_mapped_agent_and_cases_private(mapped, monkeypatch):
    engine, _, settings, _, _ = mapped
    with Session(engine) as db, db.begin():
        member = User(
            username="mapped_member", role="user", password_hash=db.get(User, 1).password_hash
        )
        db.add(member)
        db.flush()
        owner = member.id
    monkeypatch.setattr(self_service, "pin_ip", lambda *_: "93.184.216.34")
    port = ExternalService(engine, settings)
    form = self_service.Connect(
        endpoint="https://standalone.example.org/chat",
        name="Personal HTTP",
        revision="v1",
        protocol="http-json",
        access_token=uuid4().hex,
        http={"session_field": "conversation", "session_path": "conversation"},
    )
    result = port.connect(owner, form)
    entry = entry_for(settings, "external_http:" + result["registered_id"], owner=owner)
    assert port.definitions(owner)["items"][0]["protocol"] == "http-json"
    assert port.definitions(1)["items"] and not any(
        item["registered_id"] == entry.id for item in port.definitions(1)["items"]
    )
    record = port.import_case(owner, case_for(entry.id))
    assert record["runnable"]
    with pytest.raises(AgentError):
        port.trace_setup(1, entry.id)
    run = RunService(engine, settings).create(
        owner,
        CreateRun(
            request_id=uuid4(),
            target_version_id=result["version_id"],
            case_revision_ids=[record["id"]],
        ),
    )
    with pytest.raises(AgentError):
        RunService(engine, settings).read(1, run)
