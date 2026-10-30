"""Controlled scheduling over real MySQL/Redis; no provider calls or human labels."""

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_executor as control_support
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.evidence.reader import TraceReader
from app.evidence.recorder import append_event
from app.executors.budget import PLATFORM_AUTHORIZATION, ExecutorBudget, authorization_limit
from app.executors.control import ExecutionControl
from app.executors.dispatch import dispatch_pending
from app.models import TestRun, TraceEvent

infrastructure = control_support.infrastructure
run = control_support.run
pytestmark = control_support.pytestmark


def test_application_connection_uses_read_committed_without_global_change(run):
    service, _, _, _ = run
    with service.engine.connect() as connection:
        assert connection.scalar(text("SELECT @@session.transaction_isolation")) == "READ-COMMITTED"


def test_event_page_keeps_one_snapshot_while_another_run_event_commits(run):
    service, make, _, settings = run
    identifier = make()[0]
    go, done = threading.Event(), threading.Event()
    reader_thread = threading.get_ident()
    triggered = False
    errors = []

    def writer():
        try:
            assert go.wait(8)
            with Session(service.engine) as session, session.begin():
                row = session.scalar(
                    select(TestRun).where(TestRun.run_uuid == identifier).with_for_update()
                )
                append_event(session, row, None, {"kind": "run.started", "status": "running"})
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            done.set()

    def interleave(connection, cursor, statement, parameters, context, executemany):
        nonlocal triggered
        if (
            not triggered
            and threading.get_ident() == reader_thread
            and statement.startswith("SELECT")
            and "FROM ac_test_runs" in statement
        ):
            triggered = True
            go.set()
            assert done.wait(8)

    worker = threading.Thread(target=writer)
    worker.start()
    event.listen(service.engine, "after_cursor_execute", interleave)
    try:
        page = TraceReader(service.engine, settings).page(1, identifier)
    finally:
        event.remove(service.engine, "after_cursor_execute", interleave)
        worker.join(timeout=10)
    assert not errors and triggered
    assert max(item["event"]["run_seq"] for item in page["items"]) <= page["high_watermark"]
    following = TraceReader(service.engine, settings).page(1, identifier, after=page["next_cursor"])
    assert page["high_watermark"] <= following["high_watermark"]


def test_independent_run_event_writers_do_not_deadlock_on_absent_uuid_gap(run):
    service, make, _, settings = run
    runs = [make()[0] for _ in range(2)]
    identifiers = ["ffffffff-ffff-4fff-bfff-fffffffffff1", "ffffffff-ffff-4fff-bfff-fffffffffff2"]
    with Session(service.engine) as session:
        assert max(session.scalars(select(TraceEvent.event_uuid))) < identifiers[0]
    barrier = threading.Barrier(2)
    arrived = threading.local()

    def interleave(connection, cursor, statement, parameters, context, executemany):
        if (
            "FROM ac_trace_events" in statement
            and "FOR UPDATE" in statement
            and "event_uuid =" in statement
            and isinstance(parameters, dict)
            and any(value in identifiers for value in parameters.values() if isinstance(value, str))
            and not getattr(arrived, "done", False)
        ):
            arrived.done = True
            barrier.wait(timeout=8)

    def write(index):
        try:
            with Session(service.engine) as session, session.begin():
                row = session.scalar(
                    select(TestRun).where(TestRun.run_uuid == runs[index]).with_for_update()
                )
                row.status = "running"
                append_event(
                    session,
                    row,
                    None,
                    {"kind": "run.started", "status": "running"},
                    event_id=identifiers[index],
                    settings=settings,
                )
            return {"committed": True}
        except Exception as exc:
            original = getattr(exc, "orig", None)
            return {
                "committed": False,
                "type": type(exc).__name__,
                "mysql_code": original.args[0] if original else None,
            }

    event.listen(service.engine, "after_cursor_execute", interleave)
    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(write, range(2)))
    finally:
        event.remove(service.engine, "after_cursor_execute", interleave)
    assert all(result["committed"] for result in results), results
    with Session(service.engine) as session:
        assert (
            len(
                list(
                    session.scalars(
                        select(TraceEvent).where(TraceEvent.event_uuid.in_(identifiers))
                    )
                )
            )
            == 2
        )


def test_capacity_refusal_is_immediately_rescannable_after_slot_releases(run):
    service, make, _, settings = run
    identifier, first, _ = make(repetitions=2, limits={"concurrency": 1})
    sent = []
    dispatch_pending(service.engine, settings, publisher=sent.append, only_run=identifier)
    assert len(sent) == 2
    claim = service.claim(first)
    assert claim
    second = next(value for value in sent if value != first)
    assert service.claim(second) is None
    service.finish(
        claim["id"], claim["token"], "error", error={"code": "explicit_offline_capacity_control"}
    )
    sent.clear()
    dispatch_pending(service.engine, settings, publisher=sent.append, only_run=identifier)
    assert sent == [second]


def platform_budget(run, monkeypatch, *, amount=100_000):
    service, make, redis, settings = run
    settings = settings.model_copy(
        update={"executor_budget_authorization_id": PLATFORM_AUTHORIZATION}
    )
    monkeypatch.setattr(service, "settings", settings)
    identifier, execution, _ = make(budgets={"agent_microusd": amount})
    claim = service.claim(execution)
    control = ExecutionControl(service.engine, claim["id"], claim["token"])
    return ExecutorBudget(
        redis,
        settings,
        control,
        identifier,
        execution,
        prefix=settings.executor_namespace + ":t17-test:" + uuid4().hex,
    ), redis


def test_t17_approval_has_atomic_sixty_request_limit(run, monkeypatch):
    budget, redis = platform_budget(run, monkeypatch)
    assert authorization_limit(budget.settings) == 1_000_000
    redis.hset(budget.key, mapping={"limit": 1_000_000, "calls_started": 59})

    def reserve(_):
        try:
            return budget.reserve(100, 100)
        except AgentError as exc:
            assert exc.code == "model_budget_exhausted"
            return None

    with ThreadPoolExecutor(2) as pool:
        tickets = list(pool.map(reserve, range(2)))
    assert sum(t is not None for t in tickets) == 1
    assert budget.snapshot()["calls_started"] == 60


def test_t17_run_cap_and_missing_ledger_fail_closed(run, monkeypatch):
    budget, redis = platform_budget(run, monkeypatch, amount=500_000)
    with pytest.raises(AgentError) as missing:
        budget.reserve(100, 100)
    assert missing.value.code == "budget_ledger_unavailable" and not redis.exists(budget.key)
    redis.hset(budget.key, mapping={"limit": 1_000_000})
    redis.hset(budget.run_key, mapping={"charged_estimate": 99_999})
    with pytest.raises(AgentError) as capped:
        budget.reserve(100, 100)
    assert capped.value.code == "model_budget_exhausted"
    assert budget.snapshot() == {"limit": 1_000_000}


def test_redis_reservation_survives_sql_mirror_rollback(run, monkeypatch):
    budget, redis = platform_budget(run, monkeypatch)
    redis.hset(budget.key, "limit", 1_000_000)

    def broken_mirror(*_):
        raise RuntimeError("explicit SQL mirror failure after Redis reservation")

    monkeypatch.setattr(budget, "_mirror", broken_mirror)
    with pytest.raises(RuntimeError):
        budget.reserve(100, 100)
    assert budget.snapshot()["held_upper"] > 0
    assert budget.snapshot()["calls_started"] == 1
    # No provider was invoked. Unknown cross-store reservations must remain held.
    keys = list(redis.scan_iter(match=budget.execution_key + ":call:*"))
    assert len(keys) == 1 and redis.hget(keys[0], "state") == b"reserved"
