"""Free SQL/Redis controls for owned refund chat; no live provider requests."""
# ruff: noqa: F811

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_database import TEST_PASSWORD
from test_reports import lab  # noqa: F401

from app.adapters.refund_catalog import register
from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.api.booking import get_booking_service
from app.api.refund_interactive import get_refund_interaction_service
from app.interactive.refund import RefundInteractionService
from app.main import create_app
from app.services.booking import BookingService
from benchmarks.t18.offline import OfflineGateway

pytestmark = pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="isolated SQL/Redis")


@pytest.fixture(scope="module")
def service(lab):
    register(lab[0], lab[2])
    value = RefundInteractionService(
        lab[0], lab[1], lab[2], gateway_factory=lambda _: OfflineGateway()
    )
    yield value
    keys = list(lab[1].scan_iter(match=value.prefix + "*"))
    if keys:
        lab[1].delete(*keys)


def create(service):
    body = {"request_id": str(uuid4()), "version_id": service.access(1)["version_id"]}
    return service.create(1, body), body


def message(service, view, text, *, request=None):
    body = {
        "request_id": request or str(uuid4()),
        "expected_revision": view["revision"],
        "text": text,
    }
    return service.act(1, view["session_id"], body, "message"), body


def draft(service):
    view, _ = create(service)
    response, _ = message(service, view, "请为 O-1001 生成退款草稿，确认前不要入账。")
    view = response["view"]
    assert view["pending_draft"] and view["ledger"] == []
    return view


def decision(view, choice="confirm"):
    return {
        "request_id": str(uuid4()),
        "expected_revision": view["revision"],
        "decision": choice,
        "draft_id": view["pending_draft"]["draft_id"],
        "draft_hash": view["pending_draft"]["draft_hash"],
    }


def test_access_create_and_records_never_invoke_model_or_initialize_budget(service):
    budget = service.budget("access")
    before = service.redis.hgetall(budget.key)
    view, body = create(service)
    same = service.create(1, body)
    assert view["session_id"] == same["session_id"]
    assert view["messages"] == [] and view["ledger"] == [] and len(view["orders"]) == 2
    assert service.records(1, view["session_id"])["items"][0]["session_id"] == view["session_id"]
    assert service.records(99999)["items"] == []
    assert service.redis.hgetall(budget.key) == before


def test_preview_confirm_receipt_duplicate_and_history(service):
    view = draft(service)
    body = decision(view)
    response = service.act(1, view["session_id"], body, "decision")
    assert response["receipt"]["status"] == "completed"
    rows = response["view"]["ledger"]
    assert len(rows) == 1 and rows[0]["amount_fen"] == 12000
    replay = service.act(1, view["session_id"], body, "decision")
    assert replay["view"]["ledger"] == rows
    assert replay["receipt"] == response["receipt"]
    assert service.receipt(1, view["session_id"], body["request_id"])["view"]["ledger"] == rows
    assert len(service.history(1, view["session_id"])["items"]) == 4
    assert service.events(1, view["session_id"])["items"]


def test_message_cannot_bypass_explicit_confirmation_and_stale_draft_rejected(service):
    view = draft(service)
    with pytest.raises(AgentError) as confirm:
        message(service, view, "确认退款")
    assert confirm.value.code == "refund_confirmation_required"
    old = decision(view)
    response, _ = message(service, view, "改成 O-1002，不要使用旧草稿。")
    assert response["view"]["pending_draft"] is None
    with pytest.raises(AgentError):
        service.act(1, view["session_id"], old, "decision")
    assert service.view(1, view["session_id"])["ledger"] == []


def test_other_owner_and_nonrefundable_orders_are_isolated(service):
    for order in ("O-1002", "O-2001"):
        view, _ = create(service)
        response, _ = message(service, view, "查询并模拟退款 " + order)
        assert response["view"]["pending_draft"] is None and response["view"]["ledger"] == []
    with pytest.raises(AgentError) as foreign:
        service.view(99999, view["session_id"])
    assert foreign.value.status_code == 404
    with pytest.raises(AgentError):
        service.receipt(99999, view["session_id"], str(uuid4()))


def test_request_conflict_reject_and_independent_new_session(service):
    view = draft(service)
    body = decision(view, "reject")
    response = service.act(1, view["session_id"], body, "decision")
    assert response["view"]["confirmation_state"] == "rejected"
    assert response["view"]["ledger"] == []
    with pytest.raises(AgentError) as duplicate:
        service.act(1, view["session_id"], {**body, "decision": "confirm"}, "decision")
    assert duplicate.value.code == "refund_request_conflict"
    other, _ = create(service)
    assert other["scope_id"] != view["scope_id"] and other["messages"] == []
    assert len(service.view(1, view["session_id"])["messages"]) == 4


def test_orphaned_round_is_unknown_and_never_replayed(service):
    view = draft(service)
    sid = view["session_id"]
    body = decision(view)
    meta = service.owned(1, sid)
    digest = canonical_hash({"kind": "decision", **body})
    meta["inflight"] = {"request_id": body["request_id"], "token": "orphan-token", "digest": digest}
    service.redis.set(service.key(sid), json.dumps(meta))
    service.redis.set(
        service.key(sid) + ":receipt:" + body["request_id"],
        json.dumps({"request_id": body["request_id"], "digest": digest, "status": "processing"}),
    )
    result = service.receipt(1, sid, body["request_id"])
    assert result["receipt"]["status"] == "unknown" and result["view"]["uncertain"]
    assert result["view"]["read_only"] and "token" not in result["view"]["pending_request"]
    with pytest.raises(AgentError):
        service.act(1, sid, body, "decision")
    assert service.view(1, sid)["ledger"] == []


def test_active_round_lock_rejects_concurrent_submission(service):
    view = draft(service)
    body = decision(view)
    started, release = Event(), Event()

    class BlockingGateway(OfflineGateway):
        def invoke(self, *args, **kwargs):
            started.set()
            assert release.wait(10)
            return super().invoke(*args, **kwargs)

    other = RefundInteractionService(
        service.engine, service.redis, service.settings, gateway_factory=lambda _: BlockingGateway()
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(other.act, 1, view["session_id"], body, "decision")
        assert started.wait(10)
        assert service.view(1, view["session_id"])["processing"]
        try:
            with pytest.raises(AgentError) as busy:
                service.act(1, view["session_id"], body, "decision")
            assert busy.value.code == "refund_session_busy"
        finally:
            release.set()
        assert len(running.result()["view"]["ledger"]) == 1


def test_missing_ledger_is_unknown_and_not_created_on_read(service):
    view = draft(service)
    store = service.adapter.store(view["scope_id"], 1)
    backup = store.path.with_suffix(".held")
    store.path.rename(backup)
    try:
        current = service.view(1, view["session_id"])
        assert current["ledger"] is None and current["orders"] is None
        assert not store.path.exists()
    finally:
        backup.rename(store.path)


def test_http_auth_role_scope_and_extra_fields(lab, service):
    app = create_app()
    app.dependency_overrides[get_booking_service] = lambda: BookingService(lab[0])
    app.dependency_overrides[get_refund_interaction_service] = lambda: service
    view, _ = create(service)
    with TestClient(app) as client:
        base = "/api/v1/refund-interactive"
        assert client.get(base + "/access").status_code == 401
        assert (
            client.get(base + "/access", auth=("demo_reviewer", TEST_PASSWORD)).status_code == 403
        )
        assert (
            client.get(
                base + "/sessions/" + view["session_id"], auth=("t15_other", TEST_PASSWORD)
            ).status_code
            == 404
        )
        assert (
            client.get(
                base + "/sessions/" + view["session_id"], auth=("demo_operator", TEST_PASSWORD)
            ).status_code
            == 200
        )
        assert (
            client.post(
                base + "/sessions",
                auth=("demo_operator", TEST_PASSWORD),
                json={"request_id": str(uuid4()), "version_id": view["version_id"], "owner": 99999},
            ).status_code
            == 400
        )


def test_query_then_preview_and_reopen_declined_order_need_new_confirmation(service):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    class QueryGateway(OfflineGateway):
        def invoke(self, messages, tools, **kwargs):
            humans = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
            if "查询订单" in messages[humans[-1]].content and any(
                isinstance(m, ToolMessage) for m in messages[humans[-1] + 1 :]
            ):
                return AIMessage(content="明确离线控制：已查询，尚未生成退款。"), {
                    "model_requests": 0
                }
            return super().invoke(messages, tools, **kwargs)

    querying = RefundInteractionService(
        service.engine, service.redis, service.settings, gateway_factory=lambda _: QueryGateway()
    )
    view, _ = create(querying)
    result, _ = message(querying, view, "查询订单 O-1001 的状态和可退款金额。")
    assert result["view"]["pending_draft"] is None and result["view"]["ledger"] == []
    result, _ = message(querying, result["view"], "请为 O-1001 生成退款草稿，确认前不要入账。")
    first = result["view"]["pending_draft"]
    assert first and result["view"]["confirmation_state"] == "pending"
    rejected = querying.act(1, view["session_id"], decision(result["view"], "reject"), "decision")
    result, _ = message(querying, rejected["view"], "请为 O-1001 重新生成退款草稿。")
    assert result["view"]["pending_draft"]["draft_id"] != first["draft_id"]
    assert result["view"]["confirmation_state"] == "pending" and result["view"]["ledger"] == []
    assert querying.owned(1, view["session_id"])["state"]["grant"] is None
    final = querying.act(1, view["session_id"], decision(result["view"]), "decision")
    assert len(final["view"]["ledger"]) == 1


def test_failed_provider_round_is_blocked_without_automatic_replay(service):
    class BrokenGateway:
        def invoke(self, *args, **kwargs):
            raise RuntimeError("explicit offline failure")

    broken = RefundInteractionService(
        service.engine, service.redis, service.settings, gateway_factory=lambda _: BrokenGateway()
    )
    view, _ = create(broken)
    result, body = message(broken, view, "查询订单 O-1001")
    assert result["receipt"]["status"] == "failed"
    assert result["view"]["read_only"] and result["view"]["ledger"] == []
    assert broken.act(1, view["session_id"], body, "message")["receipt"] == result["receipt"]
    with pytest.raises(AgentError):
        message(broken, result["view"], "再次查询订单 O-1001")


def test_old_version_readable_but_new_action_refused(service):
    view = draft(service)
    meta = service.owned(1, view["session_id"])
    meta["version_id"] = -1
    service.redis.set(service.key(view["session_id"]), json.dumps(meta))
    history = service.view(1, view["session_id"])
    assert history["read_only"] and history["messages"] == view["messages"]
    with pytest.raises(AgentError) as rejected:
        service.act(1, view["session_id"], decision(view), "decision")
    assert rejected.value.code == "refund_version_mismatch"
    assert service.view(1, view["session_id"])["ledger"] == []


def test_refund_execution_elapsed_precision_survives_mysql_json(lab, monkeypatch):
    from itertools import cycle
    from types import SimpleNamespace

    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from test_refund_integration import execute, start

    import reference_agents.refund.agent as agent_module
    from app.evaluation.service import load_evidence
    from app.models import TestExecution

    ticks = cycle((0.0, 0.015000000013969839))
    monkeypatch.setattr(agent_module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    target = register(lab[0], lab[2])
    run, execution = start(lab, target)
    assert execute(lab, execution)["status"] == "passed"
    with Session(lab[0]) as session:
        row = session.scalar(select(TestExecution).where(TestExecution.execution_uuid == execution))
        bundle = load_evidence(session, lab[4].owned_run(session, 1, run), row, lock_events=False)
    assert bundle.complete and not bundle.integrity_errors
    assert all(
        e["payload"]["elapsed_ms"] == 15.0 for e in bundle.events if e["type"] == "tool.completed"
    )
