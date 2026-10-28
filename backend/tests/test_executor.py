"""Real MySQL/Redis control tests. Gateways here are explicitly offline doubles."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from agent_test_support import REFERENCE, OfflineGateway
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate, save_evidence

from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash
from app.api.booking import get_booking_service
from app.api.runs import get_run_service
from app.core.config import Settings
from app.db.seeds import seed_database
from app.executors.budget import AUTHORIZATION, ExecutorBudget
from app.executors.control import ExecutionControl, database_now, locked_pair
from app.executors.dispatch import dispatch_pending
from app.executors.runner import run_execution
from app.executors.service import RunService, case_content, refresh_run
from app.main import create_app
from app.models import TestCase, TestExecution, TestRun, User
from app.schemas.booking import ConfirmBooking, PreviewBooking
from app.schemas.runs import CreateRun, ExecutionInput, RecoverExecution
from app.services.booking import BookingContext, BookingService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AC_INTEGRATION") != "1", reason="Uses real MySQL/Redis, no paid requests"
    ),
]


@pytest.fixture(scope="module")
def infrastructure():
    settings = Settings().model_copy(
        update={
            "run_concurrency": 2,
            "executor_allow_paid_model_calls": True,
            "executor_budget_authorization_id": AUTHORIZATION,
            "executor_agent_budget_usd": 1.0,
            "executor_agent_budget_unlimited": False,
            "executor_namespace": "ac:t05:verify:" + uuid4().hex,
        }
    )
    redis = Redis.from_url(settings.redis_url, socket_timeout=15)
    with disposable_database("T05") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        runtime = AgentRuntime(engine, redis, settings, auto_register=True)
        yield engine, redis, settings, runtime.version_id
        with Session(engine) as session:
            for row in session.scalars(select(TestExecution)):
                runtime.saver.delete_thread(row.session_id)
        keys = list(redis.scan_iter(match=settings.executor_namespace + "*"))
        if keys:
            redis.delete(*keys)
    redis.close()


def add_case(engine, turns=None, *, approved=True, broken_hash=False, assertions=None):
    with Session(engine) as session, session.begin():
        reviewer = session.scalar(select(User).where(User.role == "reviewer"))
        row = TestCase(
            case_key="EXEC-" + uuid4().hex[:16].upper(),
            revision=1,
            family_key="development-mechanical-fixture",
            category="normal",
            dataset_split="dev",
            source="development_fixture_not_human_approval",
            turns_json=turns or [{"user": "你好，只回复一句欢迎，不要预约。"}],
            fixture_json={
                "fixture": "booking-default-v1",
                "reference_clock": REFERENCE.isoformat(),
            },
            expected_json={"assertions": assertions or [{"type": "booking_count", "equals": 0}]},
            review_status="approved" if approved else "draft",
            reviewer_id=reviewer.id if approved else None,
            reviewed_at=REFERENCE if approved else None,
            content_hash="0" * 64,
        )
        row.content_hash = "0" * 64 if broken_hash else canonical_hash(case_content(row))
        session.add(row)
        session.flush()
        return row.id


@pytest.fixture
def run(infrastructure):
    engine, redis, settings, version = infrastructure
    service = RunService(engine, settings)

    def make(*, turns=None, cases=None, assertions=None, **kwargs):
        values = CreateRun(
            request_id=uuid4(),
            target_version_id=version,
            case_revision_ids=cases or [add_case(engine, turns, assertions=assertions)],
            **kwargs,
        )
        identifier = service.create(1, values)
        return identifier, service.read(1, identifier)["executions"][0]["execution_id"], values

    yield service, make, redis, settings
    with Session(engine) as session:
        pending = list(
            session.scalars(
                select(TestRun.run_uuid).where(TestRun.status.in_(["queued", "running"]))
            )
        )
    for identifier in pending:
        service.cancel(1, identifier)


def test_manifest_idempotency_review_and_isolation(run):
    service, make, _, _ = run
    identifier, _, values = make(repetitions=2)
    assert service.create(1, values) == identifier
    view = service.read(1, identifier)
    assert len(view["executions"]) == 2
    assert len({e["scope_id"] for e in view["executions"]}) == 2
    assert len({e["session_id"] for e in view["executions"]}) == 2
    assert view["manifest_hash"] == canonical_hash(view["manifest"])
    with pytest.raises(AgentError, match="配置不同"):
        service.create(1, values.model_copy(update={"seed": 99}))
    with pytest.raises(AgentError) as error:
        service.read(2, identifier)
    assert error.value.status_code == 404


@pytest.mark.parametrize("kind", ["draft", "hash", "version", "budget"])
def test_invalid_frozen_input_rejected(run, kind):
    service, make, _, _ = run
    case = add_case(service.engine, approved=kind != "draft", broken_hash=kind == "hash")
    _, _, values = make()
    changes = {"request_id": uuid4(), "case_revision_ids": [case]}
    if kind == "version":
        changes["target_version_id"] = 99999
    if kind == "budget":
        service.settings = service.settings.model_copy(update={"executor_agent_budget_usd": 0})
    with pytest.raises(AgentError):
        service.create(1, values.model_copy(update=changes))


def test_publish_failure_is_durable_and_scan_retries(run):
    service, make, _, settings = run
    identifier, execution, _ = make()

    def unavailable(_):
        raise ConnectionError("synthetic broker unavailable")

    result = dispatch_pending(service.engine, settings, publisher=unavailable, only_run=identifier)
    assert result["failed"] == 1
    view = service.read(1, identifier)["executions"][0]
    assert view["status"] == "queued"
    assert view["timing"]["dispatch_error"]["code"] == "dispatch_error"
    sent = []
    dispatch_pending(
        service.engine, settings, publisher=sent.append, force=True, only_run=identifier
    )
    assert sent == [execution]
    save_evidence(
        "dispatch-retry.json", {"before": view, "after": service.read(1, identifier)}, task="T05"
    )


def test_claim_race_and_stale_token_cannot_write_business(run):
    service, make, _, _ = run
    identifier, execution, _ = make()
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: service.claim(execution), range(2)))
    assert sum(c is not None for c in claims) == 1
    old = next(c for c in claims if c)
    with Session(service.engine) as session, session.begin():
        e = session.get(TestExecution, old["id"])
        e.lease_until = database_now(session) - timedelta(seconds=1)
    fresh = service.claim(execution)
    assert fresh["attempt"] == 2 and fresh["session_id"] == old["session_id"]
    context = BookingContext(
        old["scope_id"], "user:1", REFERENCE, old["session_id"], old["id"], old["token"]
    )
    with pytest.raises(AgentError) as error:
        BookingService(service.engine).preview_booking(
            context,
            PreviewBooking(
                start_at=REFERENCE + timedelta(days=1, hours=5),
                end_at=REFERENCE + timedelta(days=1, hours=6),
                attendees=10,
                room_code="A101",
            ),
        )
    assert error.value.code == "lease_lost"
    assert not service.finish(old["id"], old["token"], "inconclusive")
    ExecutionControl(service.engine, fresh["id"], fresh["token"]).check()
    save_evidence(
        "claim-fence.json",
        {
            "claim_count": 1,
            "old_write": error.value.code,
            "execution": service.read(1, identifier)["executions"][0],
        },
        task="T05",
    )


def test_lease_expiring_inside_business_transaction_rolls_back(run):
    service, make, _, _ = run
    _, execution, _ = make()
    claim = service.claim(execution)
    context = BookingContext(
        claim["scope_id"], "user:1", REFERENCE, claim["session_id"], claim["id"], claim["token"]
    )

    def expire(session):
        row = session.get(TestExecution, claim["id"])
        row.lease_until = database_now(session) - timedelta(seconds=1)
        session.flush()
        return "would_commit"

    with pytest.raises(AgentError) as error:
        BookingService(service.engine)._write(context, expire)
    assert error.value.code == "lease_lost"
    # Lease update itself rolled back with the transaction.
    ExecutionControl(service.engine, claim["id"], claim["token"]).check()


def test_offline_completion_is_evaluated_from_facts_and_duplicate_noop(run):
    service, make, redis, settings = run
    identifier, execution, _ = make()
    gateway = OfflineGateway()
    gateway.queue.append(AIMessage(content="离线执行器测试，不代表真实DeepSeek。"))
    result = run_execution(
        service.engine, redis, settings, execution, gateway_factory=lambda *_: gateway
    )
    assert result["status"] == "passed"
    assert run_execution(service.engine, redis, settings, execution)["claimed"] is False
    view = service.read(1, identifier)
    assert view["status"] == "completed"
    assert view["executions"][0]["error"] is None
    assert view["executions"][0]["timing"]["gateway_mode"] == "offline_test_double"
    assert view["executions"][0]["timing"]["evaluation"]["verdict"] == "passed"


def test_wait_releases_worker_and_legitimate_input_resumes_same_session(run):
    service, make, redis, settings = run
    gateway = OfflineGateway()
    text = gateway.preview()
    identifier, execution, _ = make(
        turns=[{"user": text}], assertions=[{"type": "booking_count", "equals": 1}]
    )
    run_execution(service.engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    view = service.read(1, identifier)["executions"][0]
    assert view["status"] == "waiting_input"
    with Session(service.engine) as session:
        row = session.scalar(select(TestExecution).where(TestExecution.execution_uuid == execution))
        assert row.lease_token is None
    gateway.confirm(view["output"])
    values = ExecutionInput(
        request_id=uuid4(), expected_revision=view["timing"]["revision"], text="确认"
    )
    service.supply_input(1, identifier, execution, values)
    service.supply_input(1, identifier, execution, values)
    run_execution(service.engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    done = service.read(1, identifier)["executions"][0]
    assert done["status"] == "passed" and done["session_id"] == view["session_id"]
    assert len(done["after"]) == 1 and done["attempt"] == 1
    service.cancel(1, identifier)
    assert len(service.read(1, identifier)["executions"][0]["after"]) == 1


def test_cancellation_revokes_active_lease_and_preserves_booking(run):
    service, make, _, _ = run
    identifier, execution, _ = make()
    claim = service.claim(execution)
    context = BookingContext(
        claim["scope_id"], "user:1", REFERENCE, claim["session_id"], claim["id"], claim["token"]
    )
    booking = BookingService(service.engine)
    draft = booking.preview_booking(
        context,
        PreviewBooking(
            start_at=REFERENCE + timedelta(days=1, hours=5),
            end_at=REFERENCE + timedelta(days=1, hours=6),
            attendees=10,
            room_code="A101",
        ),
    ).data
    booking.confirm_booking(
        context,
        ConfirmBooking(
            draft_id=draft.draft_id,
            draft_hash=draft.draft_hash,
            idempotency_key=draft.suggested_idempotency_key,
            confirmed=True,
        ),
    )
    service.cancel(1, identifier)
    with pytest.raises(AgentError) as error:
        BookingService(service.engine).preview_booking(
            context,
            PreviewBooking(
                start_at=REFERENCE + timedelta(days=1, hours=5),
                end_at=REFERENCE + timedelta(days=1, hours=6),
                attendees=10,
                room_code="A101",
            ),
        )
    assert error.value.code == "execution_cancelled"
    assert service.read(1, identifier)["status"] == "cancelled"
    assert len(service.read(1, identifier)["executions"][0]["after"]) == 1


def test_atomic_budget_reservations_unknown_and_idempotent_settlement(run):
    service, make, redis, settings = run
    identifier, execution, _ = make(budgets={"agent_microusd": 5000})
    claim = service.claim(execution)
    control = ExecutionControl(service.engine, claim["id"], claim["token"])
    prefix = settings.executor_namespace + ":budget"
    budget = ExecutorBudget(redis, settings, control, identifier, execution, prefix=prefix)
    with pytest.raises(AgentError) as error:
        budget.reserve(100, 100)
    assert error.value.code == "budget_ledger_unavailable"
    redis.hset(budget.key, "limit", 5000)

    def reserve(_):
        try:
            return budget.reserve(100, 100)
        except AgentError:
            return None

    with ThreadPoolExecutor(2) as pool:
        tickets = list(pool.map(reserve, range(2)))
    assert sum(t is not None for t in tickets) == 1
    ticket = next(t for t in tickets if t)
    budget.unknown(ticket)
    assert budget.snapshot()["held_upper"] > 0
    estimate = budget.settle(ticket, {"input_tokens": 100, "output_tokens": 50})
    assert budget.settle(ticket, {"input_tokens": 100, "output_tokens": 50}) == estimate
    assert budget.snapshot()["held_upper"] == 0 and budget.snapshot()["calls_completed"] == 1
    save_evidence(
        "atomic-budget.json",
        {
            "concurrent_requests_accepted": 1,
            "ledger": budget.snapshot(),
            "execution": service.read(1, identifier),
        },
        task="T05",
    )


def test_api_auth_validation_creation_is_nonblocking_and_owner_scoped(run):
    service, make, _, _ = run
    _, _, values = make()
    app = create_app()
    app.dependency_overrides[get_run_service] = lambda: service
    app.dependency_overrides[get_booking_service] = lambda: BookingService(service.engine)
    client = TestClient(app)
    payload = values.model_copy(update={"request_id": uuid4()}).model_dump(mode="json")
    assert client.post("/api/v1/runs", json=payload).status_code == 401
    client.auth = ("demo_operator", TEST_PASSWORD)
    payload["scope_id"] = "demo"
    assert client.post("/api/v1/runs", json=payload).status_code == 400
    del payload["scope_id"]
    response = client.post("/api/v1/runs", json=payload)
    assert response.status_code == 201
    assert response.json()["data"]["status"] == "queued"
    assert response.elapsed.total_seconds() < 5


def test_explicit_recovery_preserves_cursor_scope_and_attempts(run):
    service, make, redis, settings = run
    identifier, execution, _ = make()
    gateway = OfflineGateway()
    gateway.queue.append(AgentError("model_timeout", "synthetic model timeout", 504))
    run_execution(service.engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    before = service.read(1, identifier)["executions"][0]
    service.recover(
        1, identifier, execution, RecoverExecution(request_id=uuid4(), expected_revision=0)
    )
    gateway.queue.append(AIMessage(content="恢复同一检查点，离线测试。"))
    run_execution(service.engine, redis, settings, execution, gateway_factory=lambda *_: gateway)
    after = service.read(1, identifier)["executions"][0]
    assert after["status"] == "passed" and after["attempt"] == 2
    assert after["scope_id"] == before["scope_id"] and after["session_id"] == before["session_id"]
    assert after["timing"]["cursor"] == 1


@pytest.mark.parametrize("limit", ["max_steps", "max_tokens", "max_model_calls"])
def test_hard_execution_limits_are_enforced(run, limit):
    service, make, redis, settings = run
    identifier, execution, _ = make(limits={limit: 1})
    gateway = OfflineGateway()
    if limit == "max_steps":
        gateway.preview()
        result = run_execution(
            service.engine, redis, settings, execution, gateway_factory=lambda *_: gateway
        )
        assert result["code"] == "execution_step_limit"
    else:
        claim = service.claim(execution)
        control = ExecutionControl(service.engine, claim["id"], claim["token"])
        budget = ExecutorBudget(
            redis,
            settings,
            control,
            identifier,
            execution,
            prefix=settings.executor_namespace + ":limits:" + execution,
        )
        redis.hset(budget.key, "limit", 1_000_000)
        if limit == "max_model_calls":
            budget.reserve(100, 100)
        with pytest.raises(AgentError) as error:
            budget.reserve(100, 100)
        assert error.value.code == "model_budget_exhausted"


def test_deadline_does_not_reset_on_recovery_and_heartbeat_cannot_revive_it(run):
    service, make, _, _ = run
    identifier, execution, _ = make()
    claim = service.claim(execution)
    with Session(service.engine) as session, session.begin():
        row = session.get(TestExecution, claim["id"])
        row.timing_json = {
            **row.timing_json,
            "deadline": (database_now(session) - timedelta(seconds=1)).isoformat(),
        }
        row.lease_until = database_now(session) - timedelta(seconds=1)
    with pytest.raises(AgentError):
        service.heartbeat(claim["id"], claim["token"])
    assert service.claim(execution) is None
    assert service.read(1, identifier)["executions"][0]["error"]["code"] == "execution_timeout"


def test_partial_action_without_receipt_is_not_automatically_replayed(run):
    service, make, redis, settings = run
    identifier, execution, _ = make()
    claim = service.claim(execution)
    with Session(service.engine) as session, session.begin():
        row = session.get(TestExecution, claim["id"])
        row.timing_json = {
            **row.timing_json,
            "inflight": {
                "kind": "message",
                "source": "script",
                "body": {"request_id": str(uuid4()), "expected_revision": 0, "text": "确认"},
            },
        }
        row.lease_until = database_now(session) - timedelta(seconds=1)
    gateway = OfflineGateway()
    result = run_execution(
        service.engine, redis, settings, execution, gateway_factory=lambda *_: gateway
    )
    assert result["code"] == "recovery_uncertain" and gateway.calls == []
    view = service.read(1, identifier)["executions"][0]
    assert view["attempt"] == 2 and view["session_id"] == claim["session_id"]


def test_authorization_cap_is_shared_across_different_run_locks(run):
    service, make, redis, settings = run
    prefix = settings.executor_namespace + ":cross-run:" + uuid4().hex
    budgets = []
    for _ in range(2):
        identifier, execution, _ = make(budgets={"agent_microusd": 5000})
        claim = service.claim(execution)
        control = ExecutionControl(service.engine, claim["id"], claim["token"])
        budgets.append(
            ExecutorBudget(redis, settings, control, identifier, execution, prefix=prefix)
        )
    assert budgets[0].key == budgets[1].key
    redis.hset(budgets[0].key, "limit", 5000)

    def reserve(budget):
        try:
            return budget.reserve(100, 100)
        except AgentError as exc:
            assert exc.code == "model_budget_exhausted"
            return None

    with ThreadPoolExecutor(2) as pool:
        tickets = list(pool.map(reserve, budgets))
    assert sum(ticket is not None for ticket in tickets) == 1
    assert 0 < budgets[0].snapshot()["held_upper"] <= 5000


def test_last_finisher_uses_current_statuses_despite_repeatable_read_snapshot(run):
    service, make, _, _ = run
    identifier, execution, _ = make(repetitions=2, limits={"concurrency": 2})
    rows = service.read(1, identifier)["executions"]
    first = service.claim(execution)
    second = service.claim(rows[1]["execution_id"])
    with Session(service.engine) as stale, stale.begin():
        # This is the initial nonlocking lookup in locked_pair, before another
        # finisher commits. A plain SELECT later would reuse this old snapshot.
        stale.scalar(select(TestExecution.run_id).where(TestExecution.id == second["id"]))
        assert service.finish(first["id"], first["token"], "inconclusive")
        parent, row = locked_pair(stale, second["id"])
        row.status, row.lease_token, row.lease_until = "inconclusive", None, None
        refresh_run(stale, parent)
        assert parent.status == "completed"
    assert service.read(1, identifier)["status"] == "completed"


def test_waiting_input_expires_without_new_user_input_or_model_request(run):
    service, make, _, settings = run
    identifier, execution, _ = make()
    claim = service.claim(execution)
    assert service.finish(claim["id"], claim["token"], "waiting_input")
    with Session(service.engine) as session, session.begin():
        row = session.get(TestExecution, claim["id"])
        row.timing_json = {
            **row.timing_json,
            "deadline": (database_now(session) - timedelta(seconds=1)).isoformat(),
        }
    dispatched = []
    result = dispatch_pending(service.engine, settings, publisher=dispatched.append)
    view = service.read(1, identifier)
    assert result["expired"] == 1 and dispatched == []
    assert view["status"] == "completed"
    assert view["executions"][0]["error"]["code"] == "execution_timeout"
    assert view["executions"][0]["usage"]["tickets"] == {}
