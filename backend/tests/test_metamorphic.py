"""T10: independent counterexamples, actual SQL isolation, and read-only ownership."""

import os
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_database import TEST_PASSWORD, disposable_database, migrate

from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.agents.state import FieldPatch, patch_fields
from app.agents.versions import canonical_hash
from app.api.booking import get_booking_service
from app.api.metamorphic import get_relation_service
from app.core.config import PROJECT_ROOT, Settings
from app.db.seeds import seed_database
from app.evaluation.engine import Evidence
from app.executors.budget import METAMORPHIC_AUTHORIZATION, authorization_limit
from app.executors.runner import run_execution
from app.executors.service import RunService
from app.main import create_app
from app.metamorphic.engine import (
    capacity_evidence,
    compare_relation,
    field_intent,
    interval_intent,
    observe,
)
from app.metamorphic.registration import register_catalog
from app.metamorphic.service import RelationService
from app.models import AssertionResult, TestCase, TraceEvent, User
from app.schemas.metamorphic import RelationCatalog
from app.schemas.runs import CreateRun
from app.services.booking import BookingService


def catalog():
    return RelationCatalog.model_validate_json(
        (PROJECT_ROOT / "docs/fixtures/T10/relations.json").read_bytes()
    )


def receipt(value):
    return {
        "catalog_hash": canonical_hash(value.model_dump(mode="json")),
        "decision": "confirmed",
        "source": "direct_user_confirmation",
        "statement": "synthetic test receipt, not human production approval",
        "confirmed_at": datetime.now(UTC).isoformat(),
    }


def test_catalog_exact_human_confirmed_delta_and_no_automatic_equivalence():
    original = catalog().model_dump(mode="json")
    assert len(original["relations"]) == 12
    for kind in ("swap_kind", "lose_changed_field", "unknown_member", "duplicate", "unknown_field"):
        data = deepcopy(original)
        if kind == "swap_kind":
            data["relations"][-1]["kind"] = "equivalent"
        if kind == "lose_changed_field":
            data["relations"][-1]["changed_fields"] = []
        if kind == "unknown_member":
            data["relations"][0]["variant"] = "MISSING"
        if kind == "duplicate":
            data["members"].append(data["members"][0])
        if kind == "unknown_field":
            data["relations"][0]["approved"] = True
        with pytest.raises(ValidationError):
            RelationCatalog.model_validate(data)


def test_shared_wrong_answer_missing_condition_and_corrupt_evidence_never_pass():
    relation = catalog().relations[0]
    failed = {"verdict": "failed", "evidence_refs": [], "actual": {"intent": {"attendees": 88}}}
    assert compare_relation(relation, failed, failed)["verdict"] == "failed"
    good = {
        "verdict": "passed",
        "evidence_refs": [],
        "actual": {"intent": catalog().members[0].intent.model_dump(mode="json")},
    }
    assert (
        compare_relation(relation, good, good, same_conditions=False)["verdict"] == "inconclusive"
    )
    missing = Evidence("synthetic", None, None, None, None)
    assert observe(catalog().members[0], missing, "queued")["verdict"] == "inconclusive"
    missing.integrity_errors = ["artifact hash mismatch"]
    assert observe(catalog().members[0], missing, "passed")["verdict"] == "error"


def test_normalization_equates_duration_and_timezone_without_erasing_true_change():
    a = {
        "fields": {
            "date": {"value": "2026-10-29"},
            "start_time": {"value": "14:00"},
            "duration_minutes": {"value": 60},
            "attendees": {"value": 10},
            "room_code": {"value": "A101"},
        }
    }
    assert field_intent(a) == catalog().members[0].intent.model_dump(mode="json")
    utc = {
        "start_at": "2026-10-29T06:00:00Z",
        "end_at": "2026-10-29T07:00:00+00:00",
        "attendees": 10,
        "room_code": "A101",
    }
    assert interval_intent(utc) == field_intent(a)
    a["fields"]["duration_minutes"]["value"] = 90
    assert field_intent(a)["end_time"] == "15:30"
    with pytest.raises(ValueError):
        interval_intent({**utc, "start_at": "2026-10-29T14:00:00"})


@pytest.mark.parametrize("bad_lane", ["fields", "tools", "business", "missing_query"])
def test_independent_lanes_detect_plausible_but_wrong_observations(bad_lane):
    # Independent literals: a convincing reply cannot override these observed values.
    fields = {
        "date": {"value": "2026-10-29"},
        "start_time": {"value": "14:00"},
        "end_time": {"value": "15:00"},
        "attendees": {"value": 10},
        "room_code": {"value": "A101"},
    }
    args = {
        "start_at": "2026-10-29T06:00:00Z",
        "end_at": "2026-10-29T07:00:00Z",
        "attendees": 10,
        "room_code": "A101",
    }
    sql = {
        **args,
        "booking_id": "synthetic-booking",
        "request_key": "synthetic-key",
        "room_id": 1,
        "status": "confirmed",
    }
    if bad_lane == "fields":
        fields["attendees"]["value"] = 18
    if bad_lane == "tools":
        args["attendees"] = 18
    if bad_lane == "business":
        sql["attendees"] = 18
    events = [
        {
            "event_id": "synthetic-query",
            "type": "tool.requested",
            "parent_event_id": None,
            "tool_call_id": "synthetic-call",
            "payload": {"tool": "query_availability", "arguments": args},
        }
    ]
    if bad_lane == "missing_query":
        events = []
    bundle = Evidence("synthetic-execution", [], [sql], {"fields": fields}, events, complete=True)
    result = observe(catalog().members[0], bundle, "passed")
    checks = {x["lane"]: x["passed"] for x in result["checks"]}
    assert not checks["tools" if bad_lane == "missing_query" else bad_lane]
    assert result["verdict"] == "failed"
    assert compare_relation(catalog().relations[0], result, result)["verdict"] == "failed"


def test_paraphrase_participant_unit_is_validated_without_guessing_count():
    state = {
        "fields": {},
        "last_user_text": "共10位参会者",
        "turn_index": 1,
        "reference_clock": "2026-10-28T09:00:00+08:00",
    }
    fields, _ = patch_fields(
        state, FieldPatch(attendees=10, source_quotes={"attendees": "10位参会者"})
    )
    assert fields["attendees"]["value"] == 10
    with pytest.raises(AgentError):
        patch_fields(state, FieldPatch(attendees=88, source_quotes={"attendees": "10位参会者"}))


@pytest.mark.parametrize(
    "text,quote,value,valid",
    [
        ("下午两点到三点", "三点", "15:00", True),
        ("下午两点到三点", "三点", "03:00", False),
        ("下午2点至3点", "2点", "14:00", True),
        ("上午九点到十点", "十点", "10:00", True),
        ("下午两点开会。另外三点这个时刻尚未明确上午或下午。", "三点", "15:00", False),
        ("上午九点到十点，下午三点再聊。", "九点", "21:00", False),
    ],
)
def test_range_daypart_inherits_only_within_explicit_source_range(text, quote, value, valid):
    state = {
        "fields": {},
        "last_user_text": text,
        "turn_index": 1,
        "reference_clock": "2026-10-28T09:00:00+08:00",
    }
    patch = FieldPatch(end_time=value, source_quotes={"end_time": quote})
    if valid:
        assert patch_fields(state, patch)[0]["end_time"]["value"] == value
    else:
        with pytest.raises(AgentError):
            patch_fields(state, patch)


def test_t10_approval_is_separate_and_retains_closed_switch():
    s = Settings().model_copy(
        update={
            "executor_budget_authorization_id": METAMORPHIC_AUTHORIZATION,
            "executor_agent_budget_unlimited": True,
        }
    )
    assert authorization_limit(s) == -1 and not s.executor_allow_paid_model_calls
    assert authorization_limit(s.model_copy(update={"executor_agent_budget_unlimited": False})) == 0


@pytest.mark.parametrize(
    "status,text,proves_rejection",
    [
        ("supported", "A101 容量 20 人，09:00—21:00 开放。", True),
        ("evidence_conflict", "A101 容量 20 人", False),
        ("supported", "B201 容量 40 人", False),
        ("supported", "A101 容量 40 人", False),
    ],
)
def test_capacity_refusal_needs_applicable_source_not_model_self_assurance(
    status, text, proves_rejection
):
    bundle = Evidence(
        "synthetic",
        [],
        [],
        {},
        [
            {
                "event_id": "source-event",
                "type": "retrieval.completed",
                "payload": {
                    "result": {
                        "status": status,
                        "chunks": [{"chunk_id": "rule-chunk", "text": text}],
                    }
                },
            }
        ],
        complete=True,
    )
    expected = {
        "date": "2026-10-29",
        "start_time": "14:00",
        "end_time": "15:00",
        "attendees": 21,
        "room_code": "A101",
    }
    assert bool(capacity_evidence(bundle, expected)) == proves_rejection


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("AC_INTEGRATION") != "1", reason="Real MySQL/Redis; no provider requests"
)
def test_sql_catalog_atomic_family_gate_owner_and_derived_read_only():
    s = Settings().model_copy(
        update={
            "executor_namespace": "ac:t10:test:" + uuid4().hex,
            "executor_allow_paid_model_calls": False,
        }
    )
    redis = Redis.from_url(s.redis_url)
    with disposable_database("T10") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        c = catalog()
        review = receipt(c)
        with pytest.raises(AgentError):
            register_catalog(engine, c, {**review, "catalog_hash": "wrong"})
        ids = register_catalog(engine, c, review)
        assert register_catalog(engine, c, review) == ids
        other = c.model_copy(update={"dataset_split": "holdout"})
        with pytest.raises(AgentError) as err:
            register_catalog(engine, other, receipt(other))
        assert err.value.code == "relation_family_split"
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(TestCase)) == 13
        runtime = AgentRuntime(engine, redis, s, auto_register=True)
        runs = RunService(engine, s)
        with pytest.raises(AgentError):
            runs.create(
                1,
                CreateRun(
                    request_id=uuid4(),
                    target_version_id=runtime.version_id,
                    case_revision_ids=ids[:1],
                ),
            )
        run = runs.create(
            1,
            CreateRun(
                request_id=uuid4(), target_version_id=runtime.version_id, case_revision_ids=ids
            ),
        )
        relations = RelationService(engine)
        report = relations.read(1, run)
        assert report["summary"]["counts"]["inconclusive"] == 12
        # Completed greeting-only doubles produce no fields/tools/SQL; even equal emptiness fails.
        from agent_test_support import OfflineGateway
        from langchain_core.messages import AIMessage

        for e in runs.read(1, run)["executions"]:
            gateway = OfflineGateway()
            gateway.queue.extend([AIMessage(content="离线空回复"), AIMessage(content="离线空回复")])
            run_execution(
                engine, redis, s, e["execution_id"], gateway_factory=lambda *_, g=gateway: g
            )

        def counts():
            with Session(engine) as session:
                return [
                    session.scalar(select(func.count()).select_from(t))
                    for t in (TraceEvent, AssertionResult)
                ]

        before = counts()
        assert relations.read(1, run)["summary"]["counts"]["failed"] == 12
        assert relations.read(1, run) == relations.read(1, run)
        assert counts() == before
        app = create_app()
        app.dependency_overrides[get_relation_service] = lambda: relations
        app.dependency_overrides[get_booking_service] = lambda: BookingService(engine)
        with TestClient(app) as client:
            assert client.get(f"/api/v1/runs/{run}/relations").status_code == 401
            assert (
                client.get(
                    f"/api/v1/runs/{run}/relations", auth=("demo_operator", TEST_PASSWORD)
                ).status_code
                == 200
            )
            with Session(engine) as session, session.begin():
                from app.core.passwords import hash_password

                session.add(
                    User(
                        username="t10_other",
                        role="operator",
                        enabled=True,
                        password_hash=hash_password(TEST_PASSWORD),
                    )
                )
            assert (
                client.get(
                    f"/api/v1/runs/{run}/relations", auth=("t10_other", TEST_PASSWORD)
                ).status_code
                == 404
            )
        assert counts() == before
        for e in runs.read(1, run)["executions"]:
            runtime.saver.delete_thread(e["session_id"])
        keys = list(redis.scan_iter(match=s.executor_namespace + ":*"))
        if keys:
            redis.delete(*keys)
    redis.close()
