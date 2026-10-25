"""Live infrastructure with an explicit offline gateway; paid calls are CLI-only."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from agent_test_support import REFERENCE, OfflineGateway, message, resume
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.checkpoint.redis import RedisSaver
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate, save_evidence

from app.agents.budget import AgentBudget
from app.agents.errors import AgentError
from app.agents.gateway import ModelGateway
from app.agents.retrieval import KnowledgeRetriever, digest
from app.agents.runtime import AgentRuntime
from app.api.agent import get_agent_runtime
from app.api.booking import get_booking_service
from app.core.config import Settings
from app.db.fixtures import initialize_fixture
from app.db.seeds import seed_database
from app.main import create_app
from app.models import Booking, BookingDraft, KnowledgeDocument

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AC_INTEGRATION") != "1",
        reason="Uses disposable MySQL and narrowly scoped Redis test keys",
    ),
]


@pytest.fixture(scope="module")
def infrastructure():
    settings = Settings()
    client = Redis.from_url(settings.redis_url, socket_timeout=15)
    with disposable_database("T04") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        yield engine, client, settings
    client.close()


@pytest.fixture
def fixture(infrastructure):
    engine, client, settings = infrastructure
    scope = "test:t04:" + uuid4().hex
    with Session(engine) as session, session.begin():
        initialize_fixture(session, scope)
    gateway = OfflineGateway()
    runtime = AgentRuntime(
        engine,
        client,
        settings,
        gateway=gateway,
        clock=lambda: REFERENCE,
        auto_register=True,
        prefix="ac:t04:unit:" + uuid4().hex,
    )
    view = runtime.create(1, scope_id=scope, reference_clock=REFERENCE)
    try:
        yield runtime, gateway, view
    finally:
        runtime.delete_test_session(1, view["session_id"])


def ready(fixture, **kwargs):
    runtime, gateway, view = fixture
    return runtime.act(1, view["session_id"], message(view, gateway.preview(**kwargs)))


def booking_count(runtime, scope):
    with Session(runtime.engine) as session:
        return session.scalar(
            select(func.count()).select_from(Booking).where(Booking.scope_id == scope)
        )


def test_real_checkpoint_pause_new_runtime_restore_resume_and_input_deduplication(fixture):
    runtime, gateway, initial = fixture
    paused = ready(fixture)
    assert paused["status"] == "waiting_confirmation"
    fresh_gateway = OfflineGateway()
    fresh = AgentRuntime(
        runtime.engine,
        runtime.redis,
        runtime.settings,
        gateway=fresh_gateway,
        clock=lambda: REFERENCE,
        prefix=runtime.prefix,
    )
    restored = fresh.view(1, initial["session_id"])
    assert restored["pending_draft"] == paused["pending_draft"]
    fresh_gateway.confirm(restored)
    body = resume(restored)
    result = fresh.act(1, initial["session_id"], body, kind="resume")
    replay = fresh.act(1, initial["session_id"], body, kind="resume")
    assert result["business_result"] == replay["business_result"]
    assert len(fresh_gateway.calls) == 2
    assert booking_count(runtime, runtime._metadata(1, initial["session_id"])["scope_id"]) == 1


def test_stale_checkpoint_hint_does_not_hide_a_durable_interrupt(fixture):
    runtime, _, initial = fixture
    paused = ready(fixture)
    config = runtime._config(initial["session_id"])
    checkpoint = runtime.saver.get_tuple(config)
    checkpoint_id = checkpoint.config["configurable"]["checkpoint_id"]
    key = runtime.saver._make_redis_checkpoint_key_cached(initial["session_id"], "", checkpoint_id)
    observed = runtime.redis.json().get(key)["has_writes"]
    runtime.redis.json().set(key, "$.has_writes", False)
    vanilla = RedisSaver(
        redis_client=runtime.redis,
        checkpoint_prefix="ac_t04_checkpoints",
        checkpoint_write_prefix="ac_t04_checkpoint_writes",
    )
    assert vanilla.get_tuple(config).pending_writes == []
    fixed = runtime.saver.get_tuple(config)
    assert fixed.pending_writes and runtime.graph.get_state(config).interrupts
    save_evidence(
        "checkpoint-compatibility.json",
        {
            "component": "langgraph-checkpoint-redis 0.5.2",
            "observed_has_writes_before_controlled_stale_hint": observed,
            "controlled_stale_hint": False,
            "vanilla_pending_write_count": 0,
            "fixed_pending_write_count": len(fixed.pending_writes),
            "restored_draft_id": paused["pending_draft"]["draft_id"],
            "scope": "offline gateway + real Redis writes, not real model dialogue evidence",
        },
        task="T04",
    )


def test_model_cannot_self_authorize_or_override_trusted_context(fixture):
    runtime, _, view = fixture
    paused = ready(fixture)
    _, state, _ = runtime.snapshot(1, view["session_id"])
    draft = paused["pending_draft"]
    args = {
        "draft_id": draft["draft_id"],
        "draft_hash": draft["draft_hash"],
        "idempotency_key": draft["suggested_idempotency_key"],
    }
    result = runtime.registry.dispatch(state, "confirm_booking", args)
    assert result["code"] == "confirmation_not_authorized"
    assert (
        runtime.registry.dispatch(state, "confirm_booking", {**args, "confirmed": True})["code"]
        == "tool_arguments_invalid"
    )
    assert runtime.registry.dispatch(state, "run_shell", {})["code"] == "tool_not_allowed"
    assert booking_count(runtime, state["scope_id"]) == 0


def test_refusal_revokes_draft_and_stale_resume_is_rejected(fixture):
    runtime, gateway, initial = fixture
    paused = ready(fixture)
    old = resume(paused)
    gateway.queue.append(AIMessage(content="离线夹具：已尊重拒绝，不提交。"))
    rejected = runtime.act(1, initial["session_id"], resume(paused, "reject"), kind="resume")
    assert rejected["confirmation_state"] == "rejected" and rejected["denials"]
    gateway.queue.append(AIMessage(content="离线夹具：普通回顾不重启预约。"))
    later = runtime.act(1, initial["session_id"], message(rejected, "请回顾刚才决定。"))
    assert later["confirmation_state"] == "rejected" and later["pending_draft"] is None
    with pytest.raises(AgentError):
        runtime.act(1, initial["session_id"], old, kind="resume")
    with Session(runtime.engine) as session:
        draft = session.scalar(
            select(BookingDraft).where(
                BookingDraft.draft_uuid == paused["pending_draft"]["draft_id"]
            )
        )
        assert draft.status == "rejected"


def test_sessions_same_user_keep_separate_pending_drafts_and_reject_cross_resume(fixture):
    runtime, gateway, initial = fixture
    first = ready(fixture)
    metadata = runtime._metadata(1, initial["session_id"])
    second = runtime.create(1, scope_id=metadata["scope_id"], reference_clock=REFERENCE)
    try:
        second = runtime.act(
            1, second["session_id"], message(second, gateway.preview(room="B201", attendees=30))
        )
        with Session(runtime.engine) as session:
            drafts = list(
                session.scalars(
                    select(BookingDraft).where(BookingDraft.scope_id == metadata["scope_id"])
                )
            )
            assert len(drafts) == 2 and all(draft.status == "pending" for draft in drafts)
            assert len({draft.session_id for draft in drafts}) == 2
        forged = {**resume(first), "expected_revision": second["revision"]}
        with pytest.raises(AgentError, match="当前草稿"):
            runtime.act(1, second["session_id"], forged, kind="resume")
        with pytest.raises(AgentError, match="当前身份"):
            runtime.view(2, initial["session_id"])
    finally:
        runtime.delete_test_session(1, second["session_id"])


def test_concurrent_input_has_session_lock_and_revision_conflicts(fixture):
    runtime, gateway, initial = fixture
    entered, release = Event(), Event()

    def pause(_):
        entered.set()
        assert release.wait(5)
        return AIMessage(content="离线夹具：已回应。")

    gateway.queue.append(pause)
    first = message(initial, "请先说明你能做什么")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.act, 1, initial["session_id"], first)
        try:
            assert entered.wait(5)
            with pytest.raises(AgentError, match="另一输入"):
                runtime.act(1, initial["session_id"], message(initial, "并发请求"))
        finally:
            release.set()
        future.result(timeout=5)
    with pytest.raises(AgentError, match="旧会话版本"):
        runtime.act(1, initial["session_id"], message(initial, "旧版本"))
    assert len(gateway.calls) == 1


def test_model_service_error_preserves_checkpoint_and_manual_retry(fixture):
    runtime, gateway, initial = fixture
    gateway.queue.append(AgentError("model_service_error", "离线模拟提供方错误。", 503))
    with pytest.raises(AgentError):
        runtime.act(1, initial["session_id"], message(initial, "规则是什么"))
    failed = runtime.view(1, initial["session_id"])
    assert failed["status"] == "error" and failed["next_nodes"] == ["model"]
    gateway.queue.append(AIMessage(content="离线重试恢复，不产生预约。"))
    result = runtime.act(
        1,
        initial["session_id"],
        {"request_id": str(uuid4()), "expected_revision": failed["revision"]},
        kind="retry",
    )
    assert result["status"] == "ready"


def test_retrieval_pinned_old_revision_no_answer_validity_and_conflict(infrastructure):
    engine, _, _ = infrastructure
    retriever = KnowledgeRetriever(engine)
    original = retriever.documents[0]
    result = retriever.retrieve("B201人数容量上限", REFERENCE)
    assert result["status"] == "supported"
    assert any("B201" in chunk["text"] and "40" in chunk["text"] for chunk in result["chunks"])
    assert retriever.retrieve("押金收费费用", REFERENCE)["status"] == "no_lexical_match"
    assert retriever.retrieve("A101押金收费", REFERENCE)["status"] == "low_relevance"
    assert (
        retriever.retrieve("人数", REFERENCE.replace(year=2025))["status"]
        == "no_applicable_evidence"
    )
    with Session(engine) as session, session.begin():
        document = KnowledgeDocument(
            document_key="booking-rules",
            revision=2,
            title="离线冲突夹具",
            source_type="experiment",
            source_ref="unit-test",
            valid_from=REFERENCE - timedelta(days=1),
            applicability_json=original["applicability"],
            content=original["content"].replace("80", "90"),
            content_hash=digest(original["content"].replace("80", "90")),
        )
        session.add(document)
        session.flush()
        second_id = document.id
    assert (
        KnowledgeRetriever(engine, [original["id"]]).retrieve("人数容量", REFERENCE)["status"]
        == "supported"
    )
    assert (
        KnowledgeRetriever(engine, [original["id"], second_id]).retrieve("人数容量", REFERENCE)[
            "status"
        ]
        == "evidence_conflict"
    )


@pytest.fixture
def budget(infrastructure, tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.model_probe.PROBE_PATH", tmp_path / "observation.json")
    _, redis, _ = infrastructure
    settings = Settings(
        _env_file=None,
        allow_paid_model_calls=True,
        agent_budget_usd=0.01,
        deepseek_api_key=SecretStr("offline-secret-not-a-real-key"),
        model_budget_authorization_id="T04-20261025-agent-1",
    )
    prefix = "ac:t04:unit-budget:" + uuid4().hex
    result = AgentBudget(redis, settings, prefix=prefix)
    try:
        yield result
    finally:
        keys = list(redis.scan_iter(match=prefix + ":*"))
        if keys:
            redis.delete(*keys)


def test_budget_reservations_are_atomic_and_unknown_is_not_refunded(budget):
    def reserve_one(_):
        try:
            return budget.reserve(4096, 128)
        except AgentError:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        tickets = [ticket for ticket in pool.map(reserve_one, range(12)) if ticket]
    assert len(tickets) == 1 and budget.snapshot()["held_upper"] <= 10000
    budget.unknown(tickets[0])
    reopened = AgentBudget(budget.redis, budget.settings, prefix=budget.key.rsplit(":", 2)[0])
    assert reopened.snapshot()["held_upper"] == budget.snapshot()["held_upper"]
    with pytest.raises(AgentError, match="上限"):
        budget.reserve(4096, 128)


def test_gateway_merges_stream_and_records_real_usage_shape_without_paid_call(
    budget, tmp_path, monkeypatch
):
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            assert kwargs["extra_body"]["thinking"]["type"] == "disabled"

        def stream(self, *args, **kwargs):
            yield AIMessageChunk(content="你")
            yield AIMessageChunk(
                content="好",
                usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                response_metadata={"finish_reason": "stop"},
            )

    monkeypatch.setattr("app.agents.gateway.ChatDeepSeek", Client)
    gateway = ModelGateway(budget.settings, budget, audit_directory=tmp_path)
    result, record = gateway.invoke([AIMessage(content="离线构造输入")], [])
    assert result.content == "你好" and record["estimated_peak_microusd"] == 6
    assert budget.snapshot()["held_upper"] == 0
    assert "offline-secret-not-a-real-key" not in next(tmp_path.glob("*.json")).read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "failure,code,status",
    [(TimeoutError(), "model_timeout", 504), (RuntimeError(), "model_service_error", 503)],
)
def test_gateway_service_failures_keep_unknown_reservation(
    budget, tmp_path, monkeypatch, failure, code, status
):
    class Client:
        def __init__(self, **kwargs):
            pass

        def stream(self, *args, **kwargs):
            raise failure
            yield

    monkeypatch.setattr("app.agents.gateway.ChatDeepSeek", Client)
    with pytest.raises(AgentError) as caught:
        ModelGateway(budget.settings, budget, audit_directory=tmp_path).invoke(
            [AIMessage(content="离线故障")], []
        )
    assert caught.value.code == code and caught.value.status_code == status
    assert budget.snapshot()["held_upper"] > 0 and budget.snapshot()["calls_unknown"] == 1


def test_gateway_429_is_distinct_and_preserves_reservation(budget, tmp_path, monkeypatch):
    class RateLimited(Exception):
        status_code = 429

    class Client:
        def __init__(self, **kwargs):
            pass

        def stream(self, *args, **kwargs):
            raise RateLimited()
            yield

    monkeypatch.setattr("app.agents.gateway.ChatDeepSeek", Client)
    with pytest.raises(AgentError) as caught:
        ModelGateway(budget.settings, budget, audit_directory=tmp_path).invoke(
            [AIMessage(content="离线限流")], []
        )
    assert caught.value.code == "model_rate_limited"
    assert budget.snapshot()["held_upper"] > 0


def test_gateway_missing_usage_does_not_refund_unknown_cost(budget, tmp_path, monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        def stream(self, *args, **kwargs):
            yield AIMessageChunk(
                content="离线缺失usage夹具", response_metadata={"finish_reason": "stop"}
            )

    monkeypatch.setattr("app.agents.gateway.ChatDeepSeek", Client)
    _, record = ModelGateway(budget.settings, budget, audit_directory=tmp_path).invoke(
        [AIMessage(content="离线输入")], []
    )
    assert record["estimated_peak_microusd"] is None
    assert budget.snapshot()["held_upper"] > 0 and budget.snapshot()["calls_unknown"] == 1


def test_api_uses_authenticated_session_owner_and_rejects_scope_injection(fixture):
    runtime, gateway, initial = fixture
    app = create_app()
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    app.dependency_overrides[get_booking_service] = lambda: runtime.booking
    with TestClient(app) as client:
        assert client.get("/api/v1/demo/sessions/" + initial["session_id"]).status_code == 401
        client.auth = ("demo_operator", TEST_PASSWORD)
        assert client.post("/api/v1/demo/sessions", json={"scope_id": "demo"}).status_code == 400
        response = client.get("/api/v1/demo/sessions/" + initial["session_id"])
        assert response.status_code == 200
        assert (
            client.get("/api/v1/demo/sessions/" + initial["session_id"] + "/history").json()[
                "data"
            ]["items"]
            == []
        )
