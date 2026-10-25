"""Real MySQL schema tests in disposable, narrowly named databases, never SQLite."""

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import PROJECT_ROOT, Settings
from app.core.passwords import verify_password
from app.db.engine import create_database_engine
from app.db.fixtures import FixtureError, clear_fixture, initialize_fixture
from app.db.seeds import SeedConflict, seed_database
from app.models import (
    Artifact,
    AssertionResult,
    Base,
    Booking,
    BookingDraft,
    BookingSlot,
    Defect,
    FaultProfile,
    KnowledgeDocument,
    Room,
    Target,
    TargetVersion,
    TestCase,
    TestExecution,
    TestRun,
    TraceEvent,
    User,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AC_INTEGRATION") != "1",
        reason="AC_INTEGRATION=1 creates disposable MySQL test databases",
    ),
]
TEST_PASSWORD = "synthetic-password-only-for-schema-tests"
NOW = datetime(2026, 10, 25, 1, 0, 0, 123456, tzinfo=UTC)
HASH = hashlib.sha256(b"synthetic-schema-test").hexdigest()


def save_evidence(name, data, *, task="T02"):
    directory = os.getenv(f"AC_{task}_EVIDENCE_DIR")
    if directory:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        payload = {"checked_at": datetime.now(UTC).isoformat(), **data}
        (root / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


@contextmanager
def disposable_database(task="T02"):
    settings = Settings()
    assert task in ("T02", "T03", "T04")
    prefix = f"agentchaos_{task.lower()}_verify_"
    database = prefix + uuid4().hex[:12]
    assert database.startswith(prefix) and database != settings.mysql_database
    admin = create_database_engine(settings, database="mysql")
    engine = None
    created = False
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
            created = True
        engine = create_database_engine(settings, database=database)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.exec_driver_sql(f"DROP DATABASE `{database}`")
                assert (
                    connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM information_schema.schemata "
                            "WHERE schema_name=:name"
                        ),
                        {"name": database},
                    )
                    == 0
                )
            save_evidence(
                f"cleanup-{database}.json",
                {"temporary_database": database, "removed": True},
                task=task,
            )
        admin.dispose()


def migrate(engine, direction="upgrade", revision="head"):
    with engine.connect() as connection:
        config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
        config.attributes["connection"] = connection
        getattr(command, direction)(config, revision)
        connection.commit()


@pytest.fixture(scope="module")
def database_engine():
    with disposable_database() as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        yield engine


@pytest.fixture
def session(database_engine):
    with Session(database_engine) as session:
        yield session
        session.rollback()


def insert(session, model, **values):
    row = model(**values)
    session.add(row)
    session.flush()
    return row


def rejects(session, operation, code):
    with pytest.raises(DBAPIError) as error, session.begin_nested():
        operation()
        session.flush()
    assert error.value.orig.args[0] == code


def case(session, **overrides):
    reviewer = session.scalar(select(User).where(User.role == "reviewer"))
    values = dict(
        case_key="SCHEMA-" + uuid4().hex[:16],
        revision=1,
        family_key="schema-tests",
        category="normal",
        dataset_split="dev",
        source="automated_schema_test",
        turns_json=[{"user": "仅用于数据约束验证，不调用模型。"}],
        fixture_json={"reference_clock": NOW.isoformat()},
        expected_json={"booking_count": 0},
        review_status="approved",
        reviewer_id=reviewer.id,
        reviewed_at=NOW,
        content_hash=HASH,
    )
    values.update(overrides)
    return insert(session, TestCase, **values)


def version(session, **overrides):
    target = session.scalar(select(Target))
    last = (
        session.scalar(
            select(func.max(TargetVersion.version_no)).where(TargetVersion.target_id == target.id)
        )
        or 0
    )
    values = dict(
        target_id=target.id,
        version_no=last + 1,
        model_config_json={"purpose": "schema_test", "model_requests": 0},
        prompt_hash=HASH,
        graph_revision="schema-test",
        kb_manifest_json={},
        tool_revision="schema-test",
        adapter_revision="schema-test",
        config_hash=HASH,
    )
    values.update(overrides)
    return insert(session, TargetVersion, **values)


def execution(session, *, case_row=None, scope=None):
    target_version = version(session)
    owner = session.scalar(select(User).where(User.role == "operator"))
    run = insert(
        session,
        TestRun,
        run_uuid=str(uuid4()),
        target_version_id=target_version.id,
        created_by=owner.id,
        manifest_json={"fixture": "schema-test"},
        manifest_hash=HASH,
        budget_json={"currency": "USD", "limit_microusd": 0},
    )
    case_row = case_row or case(session)
    return insert(
        session,
        TestExecution,
        execution_uuid=str(uuid4()),
        run_id=run.id,
        case_revision_id=case_row.id,
        repeat_no=1,
        seed=7,
        scope_id=scope or "test:" + uuid4().hex,
        session_id=str(uuid4()),
        input_snapshot_json={"turns": [{"user": "数据测试"}]},
    )


def booking(session, room, *, actor="actor-a", draft=None):
    draft = draft or insert(
        session,
        BookingDraft,
        draft_uuid=str(uuid4()),
        scope_id=room.scope_id,
        actor_key=actor,
        room_id=room.id,
        start_at=NOW,
        end_at=NOW + timedelta(hours=1),
        attendees=10,
        payload_hash=HASH,
        expires_at=NOW + timedelta(minutes=5),
    )
    return insert(
        session,
        Booking,
        booking_uuid=str(uuid4()),
        scope_id=room.scope_id,
        actor_key=actor,
        room_id=room.id,
        draft_id=draft.id,
        start_at=NOW,
        end_at=NOW + timedelta(hours=1),
        attendees=10,
        idempotency_key=str(uuid4()),
        payload_hash=HASH,
    )


def test_upgrade_downgrade_upgrade_and_metadata_drift():
    with disposable_database() as engine:
        migrate(engine)
        tables = inspect(engine).get_table_names()
        assert set(tables) == set(Base.metadata.tables) | {"alembic_version"}
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM information_schema.triggers "
                        "WHERE trigger_schema=DATABASE()"
                    )
                )
                == 22
            )
            config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
            config.attributes["connection"] = connection
            command.check(config)
        migrate(engine, "downgrade", "base")
        assert inspect(engine).get_table_names() == ["alembic_version"]
        migrate(engine)
        assert len(inspect(engine).get_table_names()) == 17
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.check(config)
        save_evidence(
            "migration-cycle.json",
            {
                "database": engine.url.database,
                "initial_domain_tables": 0,
                "after_upgrade": sorted(tables),
                "trigger_count": 22,
                "after_downgrade": ["alembic_version"],
                "after_reupgrade": sorted(inspect(engine).get_table_names()),
                "metadata_check_after_upgrade_and_reupgrade": "passed",
            },
        )


def test_seed_idempotency_and_no_password_reset(database_engine):
    assert seed_database(database_engine, TEST_PASSWORD) == {
        "users": 0,
        "targets": 0,
        "knowledge_documents": 0,
        "rooms": 0,
    }
    with pytest.raises(SeedConflict):
        seed_database(database_engine, "different-synthetic-password")
    with Session(database_engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 2
        for user in session.scalars(select(User)):
            assert verify_password(TEST_PASSWORD, user.password_hash)
            assert user.password_hash != TEST_PASSWORD


def test_json_snapshot_copies_input_and_preserves_chinese(session):
    original = {"model": {"notes": ["中文场地", "🏸"]}, "budget": "0.000001"}
    row = version(session, model_config_json=original)
    original["model"]["notes"][0] = "caller-mutated"
    session.expire(row)
    assert row.model_config_json["model"]["notes"] == ["中文场地", "🏸"]
    assert row.model_config_json["budget"] == "0.000001"


def test_json_input_is_copied_before_first_flush(session):
    data = {"nested": ["before"]}
    target = session.scalar(select(Target))
    row = TargetVersion(
        target_id=target.id,
        version_no=1,
        model_config_json=data,
        prompt_hash=HASH,
        graph_revision="schema-test",
        kb_manifest_json={},
        tool_revision="schema-test",
        adapter_revision="schema-test",
        config_hash=HASH,
    )
    data["nested"].append("changed-before-flush")
    session.add(row)
    session.flush()
    session.refresh(row)
    assert row.model_config_json == {"nested": ["before"]}


def test_utc_microseconds_roundtrip_and_naive_rejection(session):
    room = session.scalar(select(Room).where(Room.scope_id == "demo"))
    local = datetime(2026, 10, 25, 9, 0, 0, 123456, tzinfo=timezone(timedelta(hours=8)))
    draft = insert(
        session,
        BookingDraft,
        draft_uuid=str(uuid4()),
        scope_id="demo",
        actor_key="time-test",
        room_id=room.id,
        start_at=local,
        end_at=local + timedelta(hours=1),
        attendees=1,
        payload_hash=HASH,
        expires_at=local + timedelta(minutes=5),
    )
    session.expire(draft)
    assert draft.start_at == NOW
    raw = session.scalar(
        text("SELECT start_at FROM sb_booking_drafts WHERE id=:id"), {"id": draft.id}
    )
    assert raw == NOW.replace(tzinfo=None)
    assert session.scalar(text("SELECT @@session.time_zone")) == "+00:00"
    assert session.scalar(text("SELECT @@transaction_isolation")) == "REPEATABLE-READ"
    with pytest.raises(StatementError), session.begin_nested():
        insert(
            session,
            BookingDraft,
            draft_uuid=str(uuid4()),
            scope_id="demo",
            actor_key="naive-test",
            room_id=room.id,
            start_at=local.replace(tzinfo=None),
            end_at=local + timedelta(hours=1),
            attendees=1,
            payload_hash=HASH,
            expires_at=local + timedelta(minutes=5),
        )


def test_duplicate_keys_and_missing_foreign_key_are_rejected(session):
    room = session.scalar(select(Room).where(Room.scope_id == "demo", Room.room_code == "A101"))
    rejects(
        session,
        lambda: insert(
            session,
            Room,
            scope_id="demo",
            room_code=room.room_code,
            capacity=20,
            opening_hours_json={},
            enabled=True,
        ),
        1062,
    )
    rejects(
        session,
        lambda: insert(
            session,
            Target,
            name="invalid-owner",
            adapter_type="test",
            owner_id=2**63 - 1,
            capabilities_json={},
        ),
        1452,
    )
    rejects(
        session,
        lambda: insert(
            session,
            Room,
            scope_id="test:invalid",
            room_code="NEGATIVE",
            capacity=0,
            opening_hours_json={},
            enabled=True,
        ),
        3819,
    )


def test_draft_cannot_reference_room_from_another_scope(session):
    initialize_fixture(session, "test:scope-a")
    room = session.scalar(select(Room).where(Room.scope_id == "demo"))
    rejects(
        session,
        lambda: insert(
            session,
            BookingDraft,
            draft_uuid=str(uuid4()),
            scope_id="test:scope-a",
            actor_key="a",
            room_id=room.id,
            start_at=NOW,
            end_at=NOW + timedelta(hours=1),
            attendees=1,
            payload_hash=HASH,
            expires_at=NOW + timedelta(minutes=5),
        ),
        1452,
    )


@pytest.mark.parametrize("mismatch", ["scope", "actor", "room"])
def test_booking_cannot_reuse_draft_of_another_scope_actor_or_room(session, mismatch):
    initialize_fixture(session, "test:scope-a")
    rooms = list(
        session.scalars(select(Room).where(Room.scope_id == "test:scope-a").order_by(Room.id))
    )
    draft = insert(
        session,
        BookingDraft,
        draft_uuid=str(uuid4()),
        scope_id="test:scope-a",
        actor_key="a",
        room_id=rooms[0].id,
        start_at=NOW,
        end_at=NOW + timedelta(hours=1),
        attendees=1,
        payload_hash=HASH,
        expires_at=NOW + timedelta(minutes=5),
    )
    scope = "demo" if mismatch == "scope" else "test:scope-a"
    room = (
        session.scalar(select(Room).where(Room.scope_id == "demo"))
        if mismatch == "scope"
        else rooms[1 if mismatch == "room" else 0]
    )
    rejects(
        session,
        lambda: insert(
            session,
            Booking,
            booking_uuid=str(uuid4()),
            scope_id=scope,
            actor_key="b" if mismatch == "actor" else "a",
            room_id=room.id,
            draft_id=draft.id,
            start_at=NOW,
            end_at=NOW + timedelta(hours=1),
            attendees=1,
            idempotency_key=str(uuid4()),
            payload_hash=HASH,
        ),
        1452,
    )


def test_slot_cannot_reference_booking_in_another_room(session):
    rooms = list(session.scalars(select(Room).where(Room.scope_id == "demo").order_by(Room.id)))
    row = booking(session, rooms[0])
    start = NOW.replace(microsecond=0)
    rejects(
        session,
        lambda: insert(
            session,
            BookingSlot,
            scope_id="demo",
            room_id=rooms[1].id,
            slot_start=start,
            booking_id=row.id,
        ),
        1452,
    )
    insert(
        session,
        BookingSlot,
        scope_id="demo",
        room_id=rooms[0].id,
        slot_start=start,
        booking_id=row.id,
    )
    rejects(
        session,
        lambda: insert(
            session,
            BookingSlot,
            scope_id="demo",
            room_id=rooms[0].id,
            slot_start=start,
            booking_id=row.id,
        ),
        1062,
    )
    rejects(
        session,
        lambda: insert(
            session,
            BookingSlot,
            scope_id="demo",
            room_id=rooms[0].id,
            slot_start=start + timedelta(minutes=1),
            booking_id=row.id,
        ),
        3819,
    )


def test_draft_can_only_create_one_booking_even_with_a_new_idempotency_key(session):
    room = session.scalar(select(Room).where(Room.scope_id == "demo"))
    row = booking(session, room)
    draft = session.get(BookingDraft, row.draft_id)
    rejects(session, lambda: booking(session, room, draft=draft), 1062)


def test_demo_and_other_test_scope_survive_scoped_cleanup(session):
    initialize_fixture(session, "test:a")
    initialize_fixture(session, "test:b")
    for scope in ("demo", "test:a", "test:b"):
        room = session.scalar(select(Room).where(Room.scope_id == scope, Room.room_code == "A101"))
        row = booking(session, room)
        insert(
            session,
            BookingSlot,
            scope_id=scope,
            room_id=room.id,
            slot_start=NOW.replace(microsecond=0),
            booking_id=row.id,
        )

    def counts(scope):
        return tuple(
            session.scalar(select(func.count()).select_from(model).where(model.scope_id == scope))
            for model in (Room, BookingDraft, Booking, BookingSlot)
        )

    before = {scope: counts(scope) for scope in ("demo", "test:a", "test:b")}
    removed = clear_fixture(session, "test:a")
    after = {scope: counts(scope) for scope in ("demo", "test:a", "test:b")}
    assert counts("test:a") == (0, 0, 0, 0)
    assert counts("demo") == before["demo"] == (3, 1, 1, 1)
    assert counts("test:b") == before["test:b"] == (3, 1, 1, 1)
    assert removed == {
        "sb_booking_slots": 1,
        "sb_bookings": 1,
        "sb_booking_drafts": 1,
        "sb_rooms": 3,
    }
    assert initialize_fixture(session, "test:a") == 3
    assert initialize_fixture(session, "test:a") == 0
    save_evidence(
        "scope-isolation.json",
        {
            "database": session.get_bind().url.database,
            "columns": ["rooms", "drafts", "bookings", "slots"],
            "before": before,
            "cleared_scope": "test:a",
            "removed": removed,
            "after": after,
            "reinitialization_added": [3, 0],
            "transaction": "rolled back by test fixture; temporary database dropped after suite",
        },
    )


@pytest.mark.parametrize("scope", ["demo", "", "test:", "%", "test:x' OR 1=1", "test:" + "x" * 60])
def test_fixture_cleanup_requires_a_narrow_explicit_test_scope(session, scope):
    with pytest.raises(FixtureError):
        clear_fixture(session, scope)


def test_fixture_used_by_recorded_execution_cannot_be_cleared(session):
    initialize_fixture(session, "test:recorded")
    execution(session, scope="test:recorded")
    with pytest.raises(FixtureError):
        clear_fixture(session, "test:recorded")


def test_review_approval_requires_reviewer_and_keeps_families_together(session):
    operator = session.scalar(select(User).where(User.role == "operator"))
    rejects(session, lambda: case(session, reviewer_id=operator.id), 1644)
    approved = case(session)
    rejects(session, lambda: case(session, dataset_split="holdout"), 1644)
    rejects(
        session,
        lambda: session.execute(
            update(TestCase).where(TestCase.id == approved.id).values(source="changed")
        ),
        1644,
    )
    draft = case(session, review_status="draft", reviewer_id=None, reviewed_at=None)
    session.execute(
        update(TestCase).where(TestCase.id == draft.id).values(source="draft may change")
    )


def test_execution_requires_an_approved_case(session):
    draft = case(session, review_status="draft", reviewer_id=None, reviewed_at=None)
    rejects(session, lambda: execution(session, case_row=draft), 1644)


def test_frozen_versions_run_inputs_and_attempt_monotonicity(session):
    e = execution(session)
    run = session.get(TestRun, e.run_id)
    rejects(
        session,
        lambda: session.execute(
            update(TargetVersion)
            .where(TargetVersion.id == run.target_version_id)
            .values(graph_revision="changed")
        ),
        1644,
    )
    rejects(
        session,
        lambda: session.execute(
            update(TestRun).where(TestRun.id == run.id).values(manifest_json={"changed": True})
        ),
        1644,
    )
    rejects(
        session,
        lambda: session.execute(
            update(TestExecution).where(TestExecution.id == e.id).values(scope_id="test:changed")
        ),
        1644,
    )
    session.execute(update(TestRun).where(TestRun.id == run.id).values(status="running"))
    session.execute(
        update(TestExecution).where(TestExecution.id == e.id).values(attempt_no=2, status="running")
    )
    rejects(
        session,
        lambda: session.execute(
            update(TestExecution).where(TestExecution.id == e.id).values(attempt_no=1)
        ),
        1644,
    )


def test_knowledge_and_fault_revisions_are_insert_only(session):
    doc = session.scalar(select(KnowledgeDocument))
    rejects(
        session,
        lambda: session.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == doc.id)
            .values(content="overwritten")
        ),
        1644,
    )
    fault = insert(
        session,
        FaultProfile,
        profile_key="schema-fault",
        revision=1,
        fault_type="F01",
        trigger_stage="PRE_DISPATCH",
        target_tool="query_availability",
        parameters_json={},
        seed=1,
        max_triggers=1,
        enabled_for_test=True,
    )
    rejects(
        session,
        lambda: session.execute(
            update(FaultProfile).where(FaultProfile.id == fault.id).values(seed=2)
        ),
        1644,
    )


def test_parent_events_cannot_cross_execution_and_evidence_cannot_be_overwritten(session):
    first = execution(session)
    second = execution(session)
    event = insert(
        session,
        TraceEvent,
        event_uuid=str(uuid4()),
        execution_id=first.id,
        attempt_no=1,
        seq_no=1,
        event_type="schema.test",
        occurred_at=NOW,
        payload_json={},
        payload_hash=HASH,
    )
    rejects(
        session,
        lambda: insert(
            session,
            TraceEvent,
            event_uuid=str(uuid4()),
            execution_id=second.id,
            attempt_no=1,
            seq_no=1,
            event_type="schema.test",
            occurred_at=NOW,
            payload_json={},
            payload_hash=HASH,
            parent_event_uuid=event.event_uuid,
        ),
        1452,
    )
    rejects(
        session,
        lambda: session.execute(
            update(TraceEvent)
            .where(TraceEvent.id == event.id)
            .values(payload_json={"changed": True})
        ),
        1644,
    )
    result = insert(
        session,
        AssertionResult,
        execution_id=first.id,
        assertion_key="schema-count",
        metric_name="schema-test",
        evaluator_kind="rule",
        evaluator_version="schema-test",
        verdict="passed",
        expected_json={"count": 0},
        actual_json={"count": 0},
        evidence_refs_json=[],
        reason="Schema test only; evaluator not implemented",
    )
    rejects(
        session,
        lambda: session.execute(
            update(AssertionResult).where(AssertionResult.id == result.id).values(verdict="failed")
        ),
        1644,
    )
    artifact = insert(
        session,
        Artifact,
        run_id=first.run_id,
        kind="schema-test",
        relative_path="schema-test/no-file-created.json",
        sha256=HASH,
        size_bytes=0,
        generated_by_revision="schema-test",
        metadata_json={},
    )
    rejects(
        session,
        lambda: session.execute(
            update(Artifact).where(Artifact.id == artifact.id).values(size_bytes=1)
        ),
        1644,
    )


def test_defect_cannot_claim_execution_from_another_target(session):
    e = execution(session)
    owner = session.scalar(select(User).where(User.role == "operator"))
    other = insert(
        session,
        Target,
        name="Other schema target",
        adapter_type="schema-test",
        owner_id=owner.id,
        capabilities_json={},
    )
    rejects(
        session,
        lambda: insert(
            session,
            Defect,
            defect_key="SCHEMA-DEFECT",
            target_id=other.id,
            first_execution_id=e.id,
            category="schema-test",
            severity="test",
            fingerprint=HASH,
            evidence_refs_json=[],
        ),
        1644,
    )
