"""T03: real MySQL transactions, concurrent clients and acknowledged/unknown writes."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pymysql.err import OperationalError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate, save_evidence

from app.api.booking import booking_identity, get_booking_context, get_booking_service
from app.core.passwords import hash_password
from app.db.fixtures import initialize_fixture
from app.db.seeds import seed_database
from app.main import create_app
from app.models import Booking, BookingDraft, BookingSlot, Room, User
from app.schemas.booking import AvailabilityQuery, ConfirmBooking, PreviewBooking
from app.services.booking import SLOT, BookingContext, BookingError, BookingService
from app.tools.booking import booking_tool_contracts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AC_INTEGRATION") != "1", reason="Requires temporary MySQL database"
    ),
]
REFERENCE = datetime(2026, 10, 25, 1, tzinfo=UTC)  # 09:00 Asia/Shanghai


@pytest.fixture(scope="module")
def booking_engine():
    with disposable_database("T03") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        with Session(engine) as session, session.begin():
            session.add(
                User(
                    username="other_operator",
                    role="operator",
                    enabled=True,
                    password_hash=hash_password(TEST_PASSWORD),
                )
            )
        yield engine


@pytest.fixture
def context(booking_engine):
    scope = "test:t03:" + uuid4().hex
    with Session(booking_engine) as session, session.begin():
        initialize_fixture(session, scope)
    return BookingContext(scope, "actor-a", REFERENCE)


class Clock:
    now = REFERENCE

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def service(booking_engine, clock):
    return BookingService(booking_engine, clock=clock)


def request(*, room="A101", hour=14, duration=60, attendees=10, days=0):
    start = REFERENCE + timedelta(hours=hour - 9, days=days)
    return PreviewBooking(
        room_code=room,
        start_at=start,
        end_at=start + timedelta(minutes=duration),
        attendees=attendees,
    )


def confirmation(draft, *, key=None, **changes):
    values = dict(
        draft_id=draft.draft_id,
        draft_hash=draft.draft_hash,
        idempotency_key=key or draft.suggested_idempotency_key,
        confirmed=True,
    )
    values.update(changes)
    return ConfirmBooking(**values)


def counts(engine, context):
    with Session(engine) as session:
        return {
            model.__tablename__: session.scalar(
                select(func.count()).select_from(model).where(model.scope_id == context.scope_id)
            )
            for model in (Room, BookingDraft, Booking, BookingSlot)
        }


def error(code, operation, status=None):
    with pytest.raises(BookingError) as captured:
        operation()
    assert captured.value.code == code
    if status:
        assert captured.value.status_code == status
    return captured.value


@pytest.mark.parametrize(
    "field,value",
    [
        ("attendees", True),
        ("attendees", "10"),
        ("attendees", 0),
        ("start_at", "2026-10-25T14:00:00"),
        ("actor_key", "someone-else"),
        ("scope_id", "demo"),
        ("reference_clock", REFERENCE.isoformat()),
    ],
)
def test_input_cannot_override_context_or_use_ambiguous_types(field, value):
    body = request().model_dump()
    body[field] = value
    with pytest.raises(ValidationError):
        PreviewBooking.model_validate(body)


@pytest.mark.parametrize(
    "minutes,duration,code",
    [
        (1, 60, "time_alignment"),
        (0, 0, "invalid_duration"),
        (0, 150, "invalid_duration"),
        (-330, 30, "outside_booking_window"),
        (7 * 24 * 60, 60, "outside_booking_window"),
    ],
)
def test_time_rejections_leave_no_draft(service, context, minutes, duration, code):
    value = request()
    value = value.model_copy(
        update={
            "start_at": value.start_at + timedelta(minutes=minutes),
            "end_at": value.start_at + timedelta(minutes=minutes + duration),
        }
    )
    error(code, lambda: service.preview_booking(context, value), 400)
    assert counts(service.engine, context)["sb_booking_drafts"] == 0


def test_exact_window_duration_and_opening_boundaries(service, context):
    for duration in (30, 120):
        assert service.preview_booking(context, request(hour=9, duration=duration)).data
    shifted = replace(context, reference_clock=REFERENCE + timedelta(hours=5))
    value = request(hour=13, duration=60, days=7)
    assert service.preview_booking(
        shifted, value
    ).data.end_at == shifted.reference_clock + timedelta(days=7)
    assert service.preview_booking(context, request(room="C301", hour=17)).data
    error(
        "outside_opening_hours",
        lambda: service.preview_booking(context, request(room="C301", hour=18)),
        400,
    )


def test_availability_capacity_and_room_rules(service, context):
    value = AvailabilityQuery.model_validate(
        {**request(attendees=41).model_dump(), "room_code": None}
    )
    result = service.query_availability(context, value)
    assert [room.room_code for room in result.data.rooms] == ["C301"]
    assert result.commit_state == "not_applicable"
    assert service.query_availability(context, request(attendees=81)).data.rooms == []
    error("capacity_exceeded", lambda: service.preview_booking(context, request(attendees=21)), 400)
    error("room_not_found", lambda: service.preview_booking(context, request(room="MISSING")), 404)
    assert service.query_availability(context, request(room="MISSING")).data.rooms == []


def test_preview_is_not_a_booking_and_requires_real_confirmation(service, context, clock):
    draft = service.preview_booking(context, request()).data
    assert draft.expires_at == clock.now + timedelta(minutes=5)
    assert counts(service.engine, context)["sb_bookings"] == 0
    error(
        "confirmation_required",
        lambda: service.confirm_booking(context, confirmation(draft, confirmed=False)),
        400,
    )
    for invalid in ({"confirmed": "true"}, {"draft_hash": "x"}):
        with pytest.raises(ValidationError):
            confirmation(draft, **invalid)
    assert counts(service.engine, context)["sb_booking_slots"] == 0


def test_adjacent_intervals_succeed_and_overlaps_fail(service, context):
    first = service.preview_booking(context, request()).data
    booked = service.confirm_booking(context, confirmation(first)).data
    second = service.preview_booking(context, request(hour=15)).data
    service.confirm_booking(context, confirmation(second))
    value = request().model_copy(
        update={"start_at": request().start_at + SLOT, "end_at": request().end_at + SLOT}
    )
    error("slot_conflict", lambda: service.preview_booking(context, value))
    assert counts(service.engine, context)["sb_bookings"] == 2
    assert counts(service.engine, context)["sb_booking_slots"] == 4
    assert booked.start_at == request().start_at


@pytest.mark.parametrize("changed", ["date", "attendees", "room"])
def test_new_preview_invalidates_old_confirmation(service, context, changed):
    old = service.preview_booking(context, request()).data
    kwargs = (
        {"days": 1}
        if changed == "date"
        else ({"attendees": 11} if changed == "attendees" else {"room": "B201"})
    )
    current = service.preview_booking(context, request(**kwargs)).data
    error("draft_not_pending", lambda: service.confirm_booking(context, confirmation(old)))
    assert counts(service.engine, context)["sb_bookings"] == 0
    assert service.confirm_booking(context, confirmation(current)).data.draft_id == current.draft_id


def test_expiry_is_wall_clock_and_reference_clock_still_controls_booking(service, context, clock):
    draft = service.preview_booking(context, request()).data
    clock.now += timedelta(minutes=5)
    error("draft_expired", lambda: service.confirm_booking(context, confirmation(draft)), 410)
    current = service.preview_booking(context, request()).data
    late_context = replace(context, reference_clock=request().end_at)
    error(
        "outside_booking_window",
        lambda: service.confirm_booking(late_context, confirmation(current)),
        400,
    )
    assert service.confirm_booking(context, confirmation(current)).data


def test_confirmation_hash_and_persisted_payload_cannot_be_changed(service, context):
    draft = service.preview_booking(context, request()).data
    error(
        "draft_mismatch",
        lambda: service.confirm_booking(context, confirmation(draft, draft_hash="0" * 64)),
    )
    with Session(service.engine) as session, session.begin():
        session.execute(
            update(BookingDraft)
            .where(BookingDraft.draft_uuid == str(draft.draft_id))
            .values(attendees=11)
        )
    error("draft_mismatch", lambda: service.confirm_booking(context, confirmation(draft)))
    assert counts(service.engine, context)["sb_bookings"] == 0


def test_invalid_replacement_also_revokes_old_confirmation(service, context):
    old = service.preview_booking(context, request()).data
    error("capacity_exceeded", lambda: service.preview_booking(context, request(attendees=21)), 400)
    error("draft_not_pending", lambda: service.confirm_booking(context, confirmation(old)))
    assert counts(service.engine, context)["sb_bookings"] == 0


def test_bad_room_configuration_is_reported_as_platform_error(service, context):
    with Session(service.engine) as session, session.begin():
        session.execute(
            update(Room)
            .where(Room.scope_id == context.scope_id, Room.room_code == "A101")
            .values(opening_hours_json={"invalid": True})
        )
    error("room_rules_invalid", lambda: service.query_availability(context, request()), 503)


def test_identity_and_scope_cannot_reuse_confirm_lookup_or_cancel(service, context):
    draft = service.preview_booking(context, request()).data
    other_actor = replace(context, actor_key="actor-b")
    other_scope = replace(context, scope_id="test:t03:other-" + uuid4().hex)
    for other in (other_actor, other_scope):
        error(
            "draft_not_found",
            lambda other=other: service.confirm_booking(other, confirmation(draft)),
            404,
        )
    booked = service.confirm_booking(context, confirmation(draft)).data
    for other in (other_actor, other_scope):
        assert (
            service.lookup_booking_by_request(other, booked.idempotency_key).status == "not_found"
        )
        error(
            "booking_not_found",
            lambda other=other: service.cancel_booking(other, booked.booking_id),
            404,
        )
    assert counts(service.engine, context)["sb_booking_slots"] == 2


def test_same_request_replays_original_and_changed_parameters_are_rejected(service, context, clock):
    draft = service.preview_booking(context, request()).data
    values = confirmation(draft)
    first = service.confirm_booking(context, values).data
    clock.now += timedelta(days=8)
    late = replace(context, reference_clock=REFERENCE + timedelta(days=8))
    assert service.confirm_booking(late, values).data.booking_id == first.booking_id
    assert service.lookup_booking_by_request(context, values.idempotency_key).data == first
    error(
        "draft_not_pending",
        lambda: service.confirm_booking(context, confirmation(draft, key=uuid4())),
    )
    another = service.preview_booking(context, request(room="B201")).data
    error(
        "idempotency_conflict",
        lambda: service.confirm_booking(context, confirmation(another, key=values.idempotency_key)),
    )
    assert counts(service.engine, context)["sb_bookings"] == 1


def test_resource_occupied_after_preview_is_rechecked(service, context):
    original = service.preview_booking(context, request()).data
    competitor = replace(context, actor_key="competitor")
    other = service.preview_booking(competitor, request()).data
    service.confirm_booking(competitor, confirmation(other))
    error("slot_conflict", lambda: service.confirm_booking(context, confirmation(original)))
    assert counts(service.engine, context)["sb_bookings"] == 1


def test_room_capacity_and_enabled_status_are_rechecked(service, context):
    draft = service.preview_booking(context, request()).data
    with Session(service.engine) as session, session.begin():
        session.execute(
            update(Room)
            .where(Room.scope_id == context.scope_id, Room.room_code == "A101")
            .values(capacity=5)
        )
    error("capacity_exceeded", lambda: service.confirm_booking(context, confirmation(draft)), 400)
    with Session(service.engine) as session, session.begin():
        session.execute(
            update(Room)
            .where(Room.scope_id == context.scope_id, Room.room_code == "A101")
            .values(capacity=20, enabled=False)
        )
    error("room_disabled", lambda: service.confirm_booking(context, confirmation(draft)))
    assert counts(service.engine, context)["sb_bookings"] == 0


def test_concurrent_identical_submissions_commit_only_one_booking(service, context):
    draft = service.preview_booking(context, request()).data
    values = confirmation(draft)
    gate = Barrier(8)

    def submit(_):
        gate.wait(timeout=5)
        return service.confirm_booking(context, values)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    ids = [str(result.data.booking_id) for result in results]
    assert len(set(ids)) == 1
    after = counts(service.engine, context)
    assert after["sb_bookings"] == 1 and after["sb_booking_slots"] == 2
    save_evidence(
        "concurrent-same-request.json",
        {
            "clients": 8,
            "booking_ids": ids,
            "result_codes": [r.code for r in results],
            "rows_after": after,
        },
        task="T03",
    )


def test_concurrent_competing_actors_cannot_double_book(service, context):
    contexts = [replace(context, actor_key=f"actor-{i}") for i in range(4)]
    drafts = [service.preview_booking(c, request()).data for c in contexts]
    gate = Barrier(4)

    def submit(index):
        gate.wait(timeout=5)
        try:
            return service.confirm_booking(contexts[index], confirmation(drafts[index])).code
        except BookingError as exc:
            assert exc.code in ("slot_conflict", "booking_conflict")
            return exc.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(submit, range(4)))
    assert codes.count("booking_confirmed") == 1
    after = counts(service.engine, context)
    assert after["sb_bookings"] == 1 and after["sb_booking_slots"] == 2
    save_evidence(
        "concurrent-conflict.json", {"clients": 4, "codes": codes, "rows_after": after}, task="T03"
    )


def test_partial_slot_write_rolls_back_booking_draft_and_slots(service, context, monkeypatch):
    draft = service.preview_booking(context, request(duration=120)).data
    before = counts(service.engine, context)

    def interrupted(session, ctx, row):
        session.add(
            BookingSlot(
                scope_id=ctx.scope_id,
                room_id=row.room_id,
                slot_start=row.start_at,
                booking_id=row.id,
            )
        )
        session.flush()
        raise RuntimeError("synthetic partial-write interruption")

    with monkeypatch.context() as patch:
        patch.setattr(service, "_write_slots", interrupted)
        with pytest.raises(RuntimeError, match="synthetic partial-write"):
            service.confirm_booking(context, confirmation(draft))
    after = counts(service.engine, context)
    assert after == before
    with Session(service.engine) as session:
        row = session.scalar(
            select(BookingDraft).where(BookingDraft.draft_uuid == str(draft.draft_id))
        )
        assert row.status == "pending" and row.confirmed_at is None
    booked = service.confirm_booking(context, confirmation(draft)).data
    assert counts(service.engine, context)["sb_booking_slots"] == 4
    save_evidence(
        "transaction-rollback.json",
        {
            "before": before,
            "after_interruption": after,
            "retry_booking_id": str(booked.booking_id),
            "after_same_key_retry": counts(service.engine, context),
        },
        task="T03",
    )


def test_missing_lookup_during_inflight_commit_remains_unknown(service, context, monkeypatch):
    draft = service.preview_booking(context, request()).data
    started, resume = Event(), Event()
    original = service._write_slots

    def paused(*args):
        original(*args)
        started.set()
        assert resume.wait(timeout=10)

    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1) as pool:
        patch.setattr(service, "_write_slots", paused)
        future = pool.submit(service.confirm_booking, context, confirmation(draft))
        try:
            assert started.wait(timeout=5)
            pending = service.lookup_booking_by_request(context, draft.suggested_idempotency_key)
            assert pending.status == "not_found" and pending.commit_state == "unknown"
            busy = error(
                "booking_busy", lambda: service.confirm_booking(context, confirmation(draft)), 503
            )
            assert busy.commit_state == "unknown" and busy.retryable
        finally:
            resume.set()
        committed = future.result(timeout=5)
    assert (
        service.lookup_booking_by_request(context, draft.suggested_idempotency_key).data
        == committed.data
    )
    save_evidence(
        "inflight-lookup.json",
        {
            "lookup_while_inflight": pending.model_dump(mode="json"),
            "second_confirmation_code": busy.code,
            "second_confirmation_commit_state": busy.commit_state,
            "eventual_booking_id": str(committed.data.booking_id),
        },
        task="T03",
    )


def test_lost_commit_acknowledgment_can_be_looked_up_and_replayed(service, context, monkeypatch):
    draft = service.preview_booking(context, request()).data
    original_slots, original_commit = service._write_slots, service.engine.dialect.do_commit
    state = {"armed": False, "fired": False}

    def arm(*args):
        original_slots(*args)
        state["armed"] = True

    def lost_ack(connection):
        original_commit(connection)  # Real database commit, then discard the acknowledgment.
        if state["armed"] and not state["fired"]:
            state["fired"] = True
            raise OperationalError(2013, "synthetic commit acknowledgment lost")

    with monkeypatch.context() as patch:
        patch.setattr(service, "_write_slots", arm)
        patch.setattr(service.engine.dialect, "do_commit", lost_ack)
        outcome = error(
            "booking_state_unknown",
            lambda: service.confirm_booking(context, confirmation(draft)),
            503,
        )
    assert outcome.commit_state == "unknown" and not outcome.retryable
    found = service.lookup_booking_by_request(context, draft.suggested_idempotency_key)
    replayed = service.confirm_booking(context, confirmation(draft))
    assert state["fired"] and found.data.booking_id == replayed.data.booking_id
    after = counts(service.engine, context)
    assert after["sb_bookings"] == 1 and after["sb_booking_slots"] == 2
    save_evidence(
        "commit-acknowledgment.json",
        {
            "fault": "test-only DB commit acknowledgment loss, not T07 tool injection",
            "reported_code": outcome.code,
            "reported_commit_state": outcome.commit_state,
            "lookup_booking_id": str(found.data.booking_id),
            "same_key_replay_booking_id": str(replayed.data.booking_id),
            "rows_after": after,
        },
        task="T03",
    )


def test_cancel_is_idempotent_retains_history_and_frees_slots(service, context):
    draft = service.preview_booking(context, request()).data
    booked = service.confirm_booking(context, confirmation(draft)).data
    for _ in range(2):
        assert service.cancel_booking(context, booked.booking_id).data.status == "cancelled"
    assert counts(service.engine, context)["sb_booking_slots"] == 0
    assert counts(service.engine, context)["sb_bookings"] == 1
    assert service.confirm_booking(context, confirmation(draft)).data.status == "cancelled"
    assert (
        service.lookup_booking_by_request(context, booked.idempotency_key).data.status
        == "cancelled"
    )
    fresh = service.preview_booking(context, request()).data
    assert (
        service.confirm_booking(context, confirmation(fresh)).data.booking_id != booked.booking_id
    )


@pytest.fixture
def client(service, context):
    application = create_app()
    application.dependency_overrides[get_booking_service] = lambda: service

    def trusted_context(user_id: Annotated[int, Depends(booking_identity)]):
        return replace(context, actor_key=f"user:{user_id}")

    application.dependency_overrides[get_booking_context] = trusted_context
    with TestClient(application) as client:
        client.auth = ("demo_operator", TEST_PASSWORD)
        yield client


def test_api_authentication_permissions_and_context_injection(client):
    body = request().model_dump(mode="json")
    denied = client.post("/api/v1/booking/drafts", json=body, auth=None)
    assert denied.status_code == 401 and denied.headers["www-authenticate"] == "Basic"
    for account, password, status in (
        ("demo_operator", "synthetic-wrong", 401),
        ("demo_reviewer", TEST_PASSWORD, 403),
    ):
        response = client.post("/api/v1/booking/drafts", json=body, auth=(account, password))
        assert response.status_code == status and password not in response.text
    for protected in ("actor_key", "scope_id", "reference_clock"):
        response = client.post(
            "/api/v1/booking/drafts", json={**body, protected: "untrusted-input"}
        )
        assert response.status_code == 400 and "untrusted-input" not in response.text


def test_api_real_database_roundtrip_and_cross_identity_access(client, service, context):
    body = request().model_dump(mode="json")
    available = client.post("/api/v1/booking/availability", json=body)
    assert available.status_code == 200 and available.json()["data"]["data"]["rooms"]
    response = client.post("/api/v1/booking/drafts", json=body)
    assert response.status_code == 201
    draft = response.json()["data"]["data"]
    payload = {
        "draft_id": draft["draft_id"],
        "draft_hash": draft["draft_hash"],
        "idempotency_key": draft["suggested_idempotency_key"],
        "confirmed": True,
    }
    denied = client.post(
        "/api/v1/booking/confirm", json=payload, auth=("other_operator", TEST_PASSWORD)
    )
    assert denied.status_code == 404
    incomplete = {key: value for key, value in payload.items() if key != "confirmed"}
    assert client.post("/api/v1/booking/confirm", json=incomplete).status_code == 400
    confirmed = client.post("/api/v1/booking/confirm", json=payload)
    assert confirmed.status_code == 200
    assert confirmed.json()["request_id"] == confirmed.headers["x-request-id"]
    record = confirmed.json()["data"]["data"]
    again = client.post("/api/v1/booking/confirm", json=payload)
    assert again.json()["data"]["data"]["booking_id"] == record["booking_id"]
    route = "/api/v1/booking/requests/" + payload["idempotency_key"]
    assert client.get(route).json()["data"]["data"] == record
    assert client.get(route, auth=("other_operator", TEST_PASSWORD)).json()["data"]["data"] is None
    cancelled = client.delete("/api/v1/booking/bookings/" + record["booking_id"])
    assert cancelled.json()["data"]["data"]["status"] == "cancelled"
    assert counts(service.engine, context)["sb_booking_slots"] == 0
    save_evidence(
        "http-roundtrip.json",
        {
            "transport": "FastAPI TestClient with real temporary MySQL, not mocked persistence",
            "availability_http": available.status_code,
            "preview_http": response.status_code,
            "cross_identity_http": denied.status_code,
            "confirm": confirmed.json(),
            "same_request_retry": again.json(),
            "cancel": cancelled.json(),
            "after_cancel": counts(service.engine, context),
        },
        task="T03",
    )


def test_tool_contracts_keep_identity_out_of_model_arguments():
    contracts = booking_tool_contracts()
    assert len(contracts["tools"]) == 4 and contracts["automatic_retries"] == 0
    for tool in contracts["tools"]:
        assert not {"scope_id", "actor_key", "reference_clock", "confirmed"} & set(
            tool["input_schema"]["properties"]
        )
    assert AvailabilityQuery.model_validate(request().model_dump()).attendees == 10
