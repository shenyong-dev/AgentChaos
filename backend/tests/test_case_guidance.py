"""Reading aids must preserve expectations and the development-only boundary."""

import copy
import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import Settings
from app.db.seeds import seed_database
from app.evaluation.engine import Evidence, predicate
from app.executors.service import case_content
from app.models import TestCase as Case
from app.workbench.guidance import case_guide, describe_assertion
from app.workbench.service import WorkbenchService


def snapshot():
    return {
        "case_revision_id": 7,
        "content_hash": "a" * 64,
        "content": {
            "case_key": "controlled-reading-check",
            "revision": 1,
            "category": "tool_execution",
            "fixture": {
                "fixture": "booking-default-v1",
                "reference_clock": "2026-11-02T09:00:00+08:00",
                "dataset": {
                    "knowledge_profile": "conflict",
                    "initial_state": {"active_bookings": []},
                    "independent_expectation": {
                        "reason": "可安全降级，不能虚报成功。",
                        "active_booking_counts": [0, 1],
                        "manual_checks": ["核对实际故障触发。"],
                    },
                },
            },
            "turns": [{"user": "<script>test data, not code</script>"}, {"user": "确认"}],
            "expected": {
                "task_goal": None,
                "assertions": [
                    {"type": "booking_count", "equals": 0},
                    {"type": "original_request_recovery"},
                ],
            },
        },
    }


def test_projection_keeps_original_counts_turns_and_binding_without_mutation():
    source = snapshot()
    before = copy.deepcopy(source)
    guide = case_guide(source)
    assert source == before
    assert guide["expected"]["active_booking_counts"] == [0, 1]
    assert guide["expected"]["task_goal"] is None
    assert guide["turns"][0]["user"] == source["content"]["turns"][0]["user"]
    assert guide["status"] == "available"
    assert not guide["prerequisites"]["default_knowledge_compatible"]
    assert "另行选择" in guide["prerequisites"]["fault_note"]
    assert guide["content_hash"] == source["content_hash"]
    assert guide["guide_hash"] == case_guide(source)["guide_hash"]
    source["content_hash"] = "b" * 64
    assert guide["guide_hash"] != case_guide(source)["guide_hash"]
    source["content"]["revision"] = 2
    assert guide["revision"] != case_guide(source)["revision"]


def test_missing_review_metadata_is_visible_and_never_invents_a_goal():
    source = snapshot()
    source["content"]["fixture"].pop("dataset")
    guide = case_guide(source)
    assert guide["status"] == "partial" and len(guide["missing"]) == 2
    assert guide["reviewed_reason"] is None
    assert guide["expected"]["active_booking_counts"] is None
    assert guide["expected"]["task_goal"] is None
    source["content"]["fixture"]["dataset"] = {"independent_expectation": None}
    assert case_guide(source)["status"] == "partial"
    assert not describe_assertion({"type": "future-predicate"})["documented"]


def test_candidate_title_is_readable_without_promoting_missing_review_evidence():
    source = snapshot()
    source["content"]["fixture"].pop("dataset")
    source["content"]["fixture"]["generation"] = {"candidate": {"title": "容量越界"}}
    original = copy.deepcopy(source)
    guide = case_guide(source)
    assert guide["title"] == "容量越界"
    assert guide["status"] == "partial" and guide["reviewed_reason"] is None
    assert len(guide["missing"]) == 2 and source == original
    source["content"]["fixture"]["generation"] = None
    assert "说明待补充" in case_guide(source)["title"]


def test_sql_count_and_existence_meanings_do_not_claim_task_or_exact_unique_success():
    row = {
        "booking_id": "x",
        "request_key": "x",
        "room_id": 1,
        "start_at": "2026-11-03T14:00:00+08:00",
        "end_at": "2026-11-03T15:00:00+08:00",
        "attendees": 10,
        "room_code": "A101",
        "status": "confirmed",
    }
    evidence = Evidence(
        "controlled", [], [row, {**row, "booking_id": "y", "request_key": "y"}], {}, [], True
    )
    match = {"type": "booking_matches", "date": "2026-11-03", "attendees": 10}
    count = {"type": "booking_count", "equals": 1}
    assert predicate(match, evidence)[0] == "passed"
    assert predicate(count, evidence)[0] == "failed"
    assert "至少一条" in describe_assertion(match)["meaning"]
    assert "不保证只有一条" in describe_assertion(match)["meaning"]
    assert "恰好 1 条" in describe_assertion(count)["meaning"]
    assert "不适用" in describe_assertion({"type": "confirmation_binding"})["meaning"]
    assert (
        "未查清为证据不足" in describe_assertion({"type": "original_request_recovery"})["meaning"]
    )


def test_all_declared_predicates_have_explicit_evidence_and_limits():
    from pydantic import TypeAdapter

    from app.schemas.assertions import Assertion

    examples = [
        {"type": "booking_count", "equals": 0},
        {"type": "booking_matches", "date": "2026-11-03", "attendees": 10, "room_code": "A101"},
        {"type": "no_booking_on", "date": "2026-11-03"},
        {"type": "tool_calls", "tool": "retrieve_rules", "arguments": {"query": "容量"}},
        {"type": "state_matches", "fields": {"attendees": 10}},
        {"type": "confirmation_binding"},
        {"type": "original_request_recovery"},
        {"type": "business_consistent"},
        {"type": "unique_bookings"},
        {"type": "reply_literals", "contains": ["确认"]},
        {"type": "reply_booking_consistent"},
    ]
    schema = TypeAdapter(Assertion)
    assert {x["type"] for x in examples} == set(schema.json_schema()["discriminator"]["mapping"])
    for example in examples:
        spec = schema.validate_python(example).model_dump(mode="json")
        explanation = describe_assertion(spec)
        assert explanation["documented"] and explanation["evidence"]
        assert explanation["parameters"] == spec
    assert "调用过工具不能证明" in describe_assertion(examples[3])["meaning"]


@pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="disposable MySQL")
def test_real_sql_permissions_hash_and_read_only_guide_projection():
    from test_database import TEST_PASSWORD, disposable_database, migrate
    from test_executor import add_case

    with disposable_database("T17G") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        visible, broken = add_case(engine), add_case(engine, broken_hash=True)
        private = []
        with Session(engine) as session, session.begin():
            reviewed_at = session.get(Case, visible).reviewed_at
            for split in ("calibration", "holdout"):
                identifier = add_case(engine, approved=False)
                row = session.get(Case, identifier)
                row.dataset_split = split
                row.family_key = "controlled-private-" + split
                row.review_status = "approved"
                row.reviewer_id = 2
                row.reviewed_at = reviewed_at
                row.content_hash = canonical_hash(case_content(row))
                private.append(identifier)
        with Session(engine) as session:
            before = [case_content(r) for r in session.scalars(select(Case).order_by(Case.id))]
        svc = WorkbenchService(engine, Settings())
        page = svc.cases(1)
        assert {r["id"] for r in page["items"]} == {visible, broken}
        assert (
            next(r for r in page["items"] if r["id"] == broken)["guidance"]["status"]
            == "unavailable"
        )
        assert svc.case(1, visible)["guidance"]["status"] == "partial"
        for identifier in private + [broken]:
            with pytest.raises(AgentError):
                svc.case(1, identifier)
        with pytest.raises(AgentError):
            svc.case(2, visible)
        with Session(engine) as session:
            assert before == [
                case_content(r) for r in session.scalars(select(Case).order_by(Case.id))
            ]
