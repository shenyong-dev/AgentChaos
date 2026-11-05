"""Real owned SQL/Redis boundaries and explicitly synthetic model controls."""

import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import SecretStr, ValidationError
from redis import Redis
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import register_version
from app.api.copilot import get_copilot_service
from app.copilot.budget import AUTHORIZATION, CopilotBudget
from app.copilot.service import CopilotService
from app.copilot.sources import SourceTools, clean
from app.core.config import PROJECT_ROOT, Settings
from app.db.seeds import seed_database
from app.executors.service import RunService
from app.guidance.service import catalog
from app.main import create_app
from app.models import TestCase, TestExecution
from app.schemas.copilot import CopilotAsk, CopilotContext, CopilotCreate
from app.schemas.runs import CreateRun


def context(page="workspace", kind=None, identifier=None, **extra):
    return {
        "page": page,
        "catalog_hash": catalog()[1],
        "object_kind": kind,
        "object_id": str(identifier) if identifier else None,
        **extra,
    }


def test_minimal_context_rejects_urls_shell_arbitrary_files_and_comparison_without_selection():
    for change in (
        {"path": "C:/secret"},
        {"url": "https://untrusted.invalid"},
        {"command": "confirm"},
        {"comparison_run_id": str(uuid4())},
        {"object_kind": "sample", "object_id": "secret"},
    ):
        with pytest.raises(ValidationError):
            CopilotContext.model_validate({**context(), **change})


def test_new_question_pack_frozen_separately_from_prompt():
    import hashlib

    pack = (PROJECT_ROOT / "docs/fixtures/T17E/acceptance-questions.json").read_bytes()
    authorization = json.loads(
        (PROJECT_ROOT / "docs/fixtures/T17E/authorization.json").read_bytes()
    )
    assert hashlib.sha256(pack).hexdigest() == authorization["question_pack_sha256"]
    assert len(json.loads(pack)["questions"]) == 24
    prompt = (PROJECT_ROOT / "backend/app/copilot/prompt.txt").read_text(encoding="utf-8")
    assert "E001" not in prompt and "acceptance-questions" not in prompt


def test_question_secrets_removed_before_model_and_receipts():
    settings = Settings().model_copy(
        update={"deepseek_api_key": SecretStr("sk-owned-synthetic-secret-control")}
    )
    value = clean(
        {
            "question": (
                "password=do-not-send-this-secret Bearer abc123 "
                "sk-owned-synthetic-secret-control redis://private:pwd@127.0.0.1/0 "
                "C:\\private\\config.env"
            ),
            "password": "also-secret",
        },
        settings,
    )
    result = json.dumps(value)
    assert all(
        v not in result
        for v in [
            "do-not-send-this-secret",
            "abc123",
            "sk-owned-synthetic-secret-control",
            "also-secret",
            "private:pwd",
            "private\\\\config",
        ]
    )


@pytest.fixture(scope="module")
def lab():
    if os.getenv("AC_INTEGRATION") != "1":
        pytest.skip("AC_INTEGRATION=1 uses owned real MySQL and Redis")
    from test_database import TEST_PASSWORD, disposable_database, migrate
    from test_executor import add_case

    with disposable_database("T17E") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        settings = Settings().model_copy(
            update={
                "mysql_database": engine.url.database,
                "executor_namespace": "ac:t17e:test:" + uuid4().hex,
                "deepseek_api_key": SecretStr(""),
                "copilot_allow_paid_model_calls": False,
            }
        )
        case = add_case(engine)
        runtime = RunService(engine, settings)
        version = register_version(engine, runtime.current_manifest())
        run = runtime.create(
            1,
            CreateRun(
                request_id=uuid4(),
                target_version_id=version,
                case_revision_ids=[case],
                repetitions=1,
                seed=1771,
                budgets={"agent_microusd": 0},
            ),
        )
        with Session(engine) as db:
            execution = db.scalar(select(TestExecution.execution_uuid))
        redis = Redis.from_url(settings.redis_url)
        service = CopilotService(engine, redis, settings)
        yield {
            "engine": engine,
            "settings": settings,
            "redis": redis,
            "service": service,
            "case": case,
            "version": version,
            "run": run,
            "execution": execution,
            "password": TEST_PASSWORD,
        }
        # Only this module's uniquely bound assistant namespace is eligible for cleanup.
        keys = list(redis.scan_iter(service.prefix + ":*"))
        if keys:
            redis.delete(*keys)
        redis.close()


def create(lab, values=None, *, service=None, owner=1, binding="test-browser"):
    service = service or lab["service"]
    return service.create(
        owner, binding, CopilotCreate(request_id=uuid4(), **(values or context()))
    )


class SyntheticGateway:
    def __init__(self, mode="normal"):
        self.mode, self.calls = mode, 0
        self.requests = []

    def invoke(self, messages, tools, **kwargs):
        from langchain_core.messages import messages_to_dict

        self.requests.append(json.dumps(messages_to_dict(messages), ensure_ascii=False))
        self.calls += 1
        call = {
            "call_id": str(uuid4()),
            "role": "copilot",
            "model_requested": "synthetic-control",
            "status": "completed",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "estimated_peak_microusd": 0,
        }
        if self.mode == "failure":
            raise AgentError("model_timeout", "供应商超时控制", 504)
        if self.mode in ("forbidden", "bad_arguments"):
            return AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "execute_booking" if self.mode == "forbidden" else "read_guide",
                        "args": {} if self.mode == "forbidden" else {"path": "secret"},
                        "id": "fake-call-" + str(self.calls),
                    }
                ],
            ), call
        if self.calls == 1:
            name = next(
                (
                    v["function"]["name"]
                    for v in tools
                    if v["function"]["name"] == "read_run_evidence"
                ),
                "read_guide",
            )
            return AIMessage(
                "", tool_calls=[{"name": name, "args": {}, "id": "fake-call-" + str(self.calls)}]
            ), call
        source = json.loads(messages[-1].content)
        ref = "nonexistent" if self.mode == "forged" else source["source_id"]
        answer = {
            "facts": [{"text": "这是明确的离线控制，不代表真实模型效果。", "citations": [ref]}],
            "possible_causes": [],
            "missing_evidence": ["缺SQL不等于0笔预约。"],
            "next_steps": [],
            "action_ids": ["run.confirm"] if self.mode == "bad_action" else [],
            "certainty": "insufficient",
        }
        if self.mode in ("cited_missing", "forged_missing"):
            answer["missing_evidence"] = [
                {
                    "text": "还需核对证据",
                    "citations": [
                        source["source_id"] if self.mode == "cited_missing" else "not-a-source"
                    ],
                }
            ]
        return AIMessage(json.dumps(answer, ensure_ascii=False)), call


def controlled(lab, mode="normal"):
    gateway = SyntheticGateway(mode)
    return CopilotService(
        lab["engine"], lab["redis"], lab["settings"], gateway_factory=lambda _: gateway
    ), gateway


def ask(
    service,
    created,
    question="下一步是什么？",
    request_id=None,
    *,
    owner=1,
    binding="test-browser",
    check=None,
):
    return service.ask(
        owner,
        binding,
        created["session_id"],
        CopilotAsk(
            request_id=request_id or uuid4(),
            context_hash=created["context_hash"],
            question=question,
        ),
        check=check,
    )


def test_free_browsing_and_disabled_model_do_not_create_calls_or_business_writes(lab):
    created = create(lab)
    result = ask(lab["service"], created)
    assert result["mode"] == "deterministic" and result["status"] == "unavailable"
    assert result["calls"] == [] and result["answer"] is None
    assert result["sources"][0]["tool"] == "read_guide"


def test_normal_loop_cites_owned_sql_and_events_without_inventing_zero(lab):
    service, gateway = controlled(lab)
    created = create(lab, context("runs", "execution", lab["execution"]), service=service)
    result = ask(service, created)
    assert result["status"] == "completed" and gateway.calls == 2
    source = next(s for s in result["sources"] if s["tool"] == "read_run_evidence")
    assert source["content"]["executions_preview"][0]["after_sql_snapshot"] is None
    assert source["content"]["sql_missing_is_not_zero"] is True
    assert set(result["answer"]["facts"][0]["citations"]).issubset(
        {s["source_id"] for s in result["sources"]}
    )


@pytest.mark.parametrize(
    "mode,code",
    [
        ("forged", "copilot_reference_invalid"),
        ("forbidden", "copilot_tool_forbidden"),
        ("bad_arguments", "copilot_tool_forbidden"),
        ("bad_action", "copilot_action_invalid"),
        ("failure", "model_timeout"),
    ],
)
def test_bad_references_write_tools_parameters_actions_and_provider_failures_are_visible(
    lab, mode, code
):
    service, _ = controlled(lab, mode)
    created = create(lab, service=service)
    result = ask(service, created)
    assert result["status"] == "failed" and result["answer"] is None
    assert result["error_code"] == code
    assert result["sources"][0]["tool"] == "read_guide"


def test_idempotent_question_replays_original_and_conflicting_body_does_not_pay_twice(lab):
    service, gateway = controlled(lab)
    created = create(lab, service=service)
    request = uuid4()
    result = ask(service, created, request_id=request)
    assert ask(service, created, request_id=request) == result and gateway.calls == 2
    with pytest.raises(AgentError, match="同一请求标识"):
        ask(service, created, question="换一个问题", request_id=request)
    assert gateway.calls == 2


@pytest.mark.parametrize("binding,owner", [("different-browser", 1), ("test-browser", 2)])
def test_other_browser_and_other_identity_cannot_read_original_receipts(lab, binding, owner):
    created = create(lab)
    with pytest.raises(AgentError):
        lab["service"].load(owner, binding, created["session_id"])


def test_context_hash_and_deleted_selected_case_are_rechecked(lab):
    created = create(lab, context("library", "case", lab["case"]))
    with pytest.raises(AgentError, match="页面或对象"):
        lab["service"].ask(
            1,
            "test-browser",
            created["session_id"],
            CopilotAsk(request_id=uuid4(), context_hash="0" * 64, question="下一步"),
        )


@pytest.mark.parametrize("page", ["judging", "semantic"])
def test_independent_pages_never_send_questions_to_a_model_even_with_gateway(lab, page):
    service, gateway = controlled(lab)
    created = create(lab, context(page), service=service, owner=2)
    result = ask(service, created, "样本应该选支持吗？", owner=2)
    assert result["status"] == "restricted" and gateway.calls == 0 and result["answer"] is None


def test_holdout_or_unapproved_case_cannot_be_bound(lab):
    from test_executor import add_case

    draft = add_case(lab["engine"], approved=False)
    with pytest.raises(AgentError):
        create(lab, context("library", "case", draft))


def test_stale_source_revokes_answer_and_retains_usage_control(lab):
    from datetime import UTC, datetime, timedelta

    service, gateway = controlled(lab)
    original = service.tools_factory

    def old_tools(owner, values):
        tool = original(owner, values)
        tool.started = datetime.now(UTC) - timedelta(minutes=10)
        return tool

    service.tools_factory = old_tools
    created = create(lab, service=service)
    result = ask(service, created)
    assert result["status"] == "failed" and result["error_code"] == "copilot_sources_expired"
    assert gateway.calls == 2 and len(result["calls"]) == 2


def test_real_redis_budget_caps_missing_ledger_unknown_reservation_and_settlement(lab, tmp_path):
    settings = lab["settings"].model_copy(
        update={
            "copilot_allow_paid_model_calls": True,
            "copilot_budget_authorization_id": AUTHORIZATION,
            "copilot_budget_usd": 1,
        }
    )
    budget = CopilotBudget(lab["redis"], settings, str(uuid4()), directory=tmp_path)
    budget.key = lab["service"].prefix + ":budget-control:" + uuid4().hex
    budget.session_key = budget.key + ":session"
    budget.calls_key = budget.session_key + ":calls"
    try:
        with pytest.raises(AgentError):
            budget.reserve(100, 100)
        lab["redis"].hset(budget.key, mapping={"limit": 1_000_000})
        first = budget.reserve(100, 100)
        budget.unknown(first)
        assert int(lab["redis"].hget(budget.key, "held_upper")) == first["upper_microusd"]
        budget.settle(first, {"input_tokens": 50, "output_tokens": 20})
        assert int(lab["redis"].hget(budget.key, "held_upper")) == 0
        assert int(lab["redis"].hget(budget.key, "calls_completed")) == 1
        lab["redis"].hset(budget.key, "calls_started", 60)
        with pytest.raises(AgentError):
            budget.reserve(100, 100)
    finally:
        keys = list(lab["redis"].scan_iter(budget.key + "*"))
        if keys:
            lab["redis"].delete(*keys)


def test_csrf_and_anonymous_copilot_calls_are_rejected(lab):
    from app.api.cases import get_case_service
    from app.auth.service import SessionService
    from app.cases.service import CaseService

    app = create_app()
    app.dependency_overrides[get_copilot_service] = lambda: lab["service"]
    app.dependency_overrides[get_case_service] = lambda: CaseService(
        lab["engine"], lab["redis"], lab["settings"]
    )
    service = SessionService(lab["engine"], lab["redis"], lab["settings"])
    import app.auth.service as auth_module

    original = auth_module.get_session_service
    auth_module.get_session_service = lambda: service
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/copilot/access").status_code == 401
            response = client.post(
                "/api/v1/copilot/sessions",
                json={**context(), "request_id": str(uuid4())},
                headers={"X-AC-Origin": "http://127.0.0.1:5173"},
            )
            assert response.status_code == 401
    finally:
        auth_module.get_session_service = original


def test_context_tools_work_under_real_select_only_mysql_grant(lab):
    from app.db.engine import create_database_engine
    from app.reports.service import ReportService

    runtime = RunService(lab["engine"], lab["settings"])
    comparisons = []
    for seed in (91, 92):
        identifier = runtime.create(
            1,
            CreateRun(
                request_id=uuid4(),
                target_version_id=lab["version"],
                case_revision_ids=[lab["case"]],
                seed=seed,
                budgets={"agent_microusd": 0},
            ),
        )
        runtime.cancel(1, identifier)
        comparisons.append(identifier)

    username = "ac_e_" + uuid4().hex[:12]
    password = uuid4().hex + uuid4().hex
    admin = create_database_engine(Settings(), database="mysql")
    reader = None
    created = False
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(
                text(f"CREATE USER '{username}'@'%' IDENTIFIED BY :password"),
                {"password": password},
            )
            created = True
            connection.execute(
                text(f"GRANT SELECT ON `{lab['engine'].url.database}`.* TO '{username}'@'%'")
            )
        settings = lab["settings"].model_copy(
            update={"mysql_user": username, "mysql_password": SecretStr(password)}
        )
        reader = create_database_engine(settings)
        tools = SourceTools(
            reader, settings, 1, CopilotContext(**context("runs", "execution", lab["execution"]))
        )
        source = tools.invoke("read_run_evidence", {})
        assert source["content"]["executions_preview"][0]["execution_id"] == lab["execution"]
        compared = ReportService(reader, settings).comparison(1, *comparisons)
        assert (
            next(c for c in compared["checks"] if c["condition"] == "seed")["status"] == "mismatch"
        )
        comparison_tools = SourceTools(
            reader,
            settings,
            1,
            CopilotContext(
                **context("reports", "run", comparisons[0], comparison_run_id=comparisons[1])
            ),
        )
        source = comparison_tools.invoke("read_comparison", {})
        assert source["content"]["conditions_match"] is False
        assert (
            next(c for c in source["content"]["checks"] if c["condition"] == "seed")["status"]
            == "mismatch"
        )
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError):
            with reader.connect() as connection:
                connection.execute(text("DELETE FROM sb_rooms WHERE 1=0"))
    finally:
        if reader:
            reader.dispose()
        if created:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(text(f"DROP USER '{username}'@'%'"))
        admin.dispose()


def test_other_owner_run_and_protected_dataset_are_rejected_before_gateway(lab):
    from app.agents.versions import canonical_hash
    from app.executors.service import case_content

    with pytest.raises(AgentError):
        create(lab, context("runs", "run", lab["run"]), owner=2)
    with Session(lab["engine"]) as session, session.begin():
        source = session.get(TestCase, lab["case"])
        item = {
            c.name: getattr(source, c.name)
            for c in TestCase.__table__.columns
            if c.name not in ("id", "created_at", "updated_at")
        }
        item.update(
            {
                "case_key": "E-HOLDOUT-" + uuid4().hex[:8],
                "family_key": "T17E-holdout-" + uuid4().hex,
                "dataset_split": "holdout",
            }
        )
        row = TestCase(**item)
        row.content_hash = canonical_hash(case_content(row))
        session.add(row)
        session.flush()
        identifier = row.id
    with pytest.raises(AgentError):
        create(lab, context("library", "case", identifier))


def test_sensitive_data_does_not_reach_control_gateway_or_immutable_receipt(lab):
    service, gateway = controlled(lab)
    created = create(lab, service=service)
    result = ask(service, created, "password=do-not-send-this-secret 请解释此页")
    assert "do-not-send-this-secret" not in json.dumps(result)
    assert "[REDACTED]" in result["question"] and gateway.calls == 2


def test_auth_revocation_mid_request_retains_receipt_but_does_not_deliver(lab):
    service, gateway = controlled(lab)
    created = create(lab, service=service)
    request_id = uuid4()
    checks = 0

    def revoked():
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise AgentError("session_expired", "已退出", 401)

    with pytest.raises(AgentError, match="已退出"):
        ask(service, created, request_id=request_id, check=revoked)
    assert gateway.calls == 1
    receipt = service.read_turn(1, "test-browser", created["session_id"], request_id)
    assert receipt["status"] == "failed" and len(receipt["calls"]) == 1


def test_selection_free_text_is_not_in_first_request_or_session_metadata(lab):
    service, gateway = controlled(lab)
    original = service.tools_factory

    def altered(owner, values):
        tools = original(owner, values)
        tools.observation["selection"]["summary"] = "password=selection-must-not-leak"
        tools.observation["selection"]["facts"] = ["sk-owned-selection-secret-control"]
        return tools

    service.tools_factory = altered
    created = create(lab, context("library", "case", lab["case"]), service=service)
    stored = lab["redis"].get(service.key(created["session_id"]))
    assert b"selection-must-not-leak" not in stored
    assert "summary" not in created["selected"] and "facts" not in created["selected"]
    result = ask(service, created)
    assert result["status"] == "completed"
    assert "selection-must-not-leak" not in gateway.requests[0]
    assert "sk-owned-selection-secret-control" not in gateway.requests[0]


@pytest.mark.parametrize(
    "mode,expected", [("cited_missing", "completed"), ("forged_missing", "failed")]
)
def test_cited_missing_evidence_is_typed_and_its_sources_are_also_validated(lab, mode, expected):
    service, _ = controlled(lab, mode)
    result = ask(service, create(lab, service=service))
    assert result["status"] == expected
    if expected == "failed":
        assert result["error_code"] == "copilot_reference_invalid" and result["answer"] is None


def test_total_async_deadline_cancels_waiting_for_next_chunk_and_retains_unknown(
    tmp_path, monkeypatch
):
    import asyncio
    import time

    from langchain_core.messages import AIMessageChunk, HumanMessage

    from app.agents.gateway import ModelGateway

    state = {"closed": False, "client_closed": False, "reserved": 0, "unknown": 0, "settled": 0}

    class Client:
        async def close(self):
            state["client_closed"] = True

    class Stream:
        root_async_client = Client()

        async def astream(self, *args, **kwargs):
            try:
                yield AIMessageChunk(content="begin")
                await asyncio.sleep(10)
                yield AIMessageChunk(content="too late")
            finally:
                state["closed"] = True

    class Budget:
        def reserve(self, *args):
            state["reserved"] += 1
            return {"call_id": str(uuid4()), "upper_microusd": 1000}

        def settle(self, *args):
            state["settled"] += 1

        def unknown(self, *args):
            state["unknown"] += 1

    monkeypatch.setattr("app.agents.gateway.ChatDeepSeek", lambda **kwargs: Stream())
    settings = Settings().model_copy(
        update={"deepseek_api_key": SecretStr("synthetic-async-no-provider")}
    )
    gateway = ModelGateway(settings, Budget(), role="copilot", audit_directory=tmp_path)
    start = time.monotonic()
    with pytest.raises(AgentError) as caught:
        gateway.invoke([HumanMessage("bounded control")], [], deadline=start + 0.08)
    assert caught.value.code == "model_timeout"
    assert time.monotonic() - start < 0.7
    assert state == {
        "closed": True,
        "client_closed": True,
        "reserved": 1,
        "unknown": 1,
        "settled": 0,
    }


def test_expired_assistant_session_is_not_resurrected_by_finishing_turn(lab):
    service, gateway = controlled(lab)
    created = create(lab, service=service)
    checks = 0

    def expire():
        nonlocal checks
        checks += 1
        if checks == 4:
            lab["redis"].delete(service.key(created["session_id"]))

    with pytest.raises(AgentError):
        ask(service, created, check=expire)
    assert lab["redis"].get(service.key(created["session_id"])) is None


def test_oversized_event_preview_keeps_sql_unknown_visible_instead_of_erasing_it(lab, monkeypatch):
    from app.evidence.reader import TraceReader

    def oversized(self, owner, run, event):
        return {
            "event": {
                "event_id": str(event),
                "type": "explicit-oversized-log-control",
                "payload": {"untrusted_log": "X" * 15000},
            }
        }

    monkeypatch.setattr(TraceReader, "event", oversized)
    tools = SourceTools(
        lab["engine"], lab["settings"], 1, CopilotContext(**context("runs", "run", lab["run"]))
    )
    source = tools.invoke("read_run_evidence", {})
    assert source["content"]["truncated"] is True
    assert '"after_sql_snapshot": null' in source["content"]["preview"]
    assert "仅提供有界预览" in source["content"]["note"]
