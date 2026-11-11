"""Missing/contradictory evidence must never become successful recovery."""

from copy import deepcopy

import pytest

from app.agents.versions import canonical_hash
from app.reports.fault_pairs import describe, fault_receipts, goal_matches


@pytest.fixture
def observed():
    goal = {
        "room_code": "A101",
        "start_at": "2026-11-08T06:00:00+00:00",
        "end_at": "2026-11-08T07:00:00+00:00",
        "attendees": 8,
    }
    booking = {**goal, "booking_id": "booking-1", "request_key": "key-1", "status": "confirmed"}
    spec = {
        "fault_type": "F04",
        "trigger_stage": "POST_COMMIT",
        "target_tool": "confirm_booking",
        "max_triggers": 1,
    }
    profile = {"config": spec, "config_hash": canonical_hash(spec)}

    def event(i, kind, payload, parent=None):
        return {
            "event_id": str(i),
            "run_seq": i,
            "type": kind,
            "payload": payload,
            "parent_event_id": str(parent) if parent else None,
            "tool_call_id": "call-1",
        }

    payload = {
        "profile_hash": profile["config_hash"],
        "fault_type": "F04",
        "stage": "POST_COMMIT",
        "raw_result": {"commit_state": "committed", "data": {"booking_id": "booking-1"}},
        "visible_result": {"commit_state": "unknown", "request_key": "key-1"},
        "after": [booking],
    }
    events = [
        event(1, "input.received", {"text": "确认"}),
        event(
            2,
            "tool.requested",
            {"tool": "confirm_booking", "arguments": {"idempotency_key": "key-1"}},
        ),
        event(3, "fault.injected", payload, 2),
        event(
            4,
            "tool.error",
            {
                "fault_event_id": "3",
                "raw_result": payload["raw_result"],
                "visible_result": payload["visible_result"],
            },
            2,
        ),
        event(
            5,
            "tool.requested",
            {"tool": "lookup_booking_by_request", "arguments": {"idempotency_key": "key-1"}},
        ),
        event(
            6,
            "tool.completed",
            {"visible_result": {"data": {"booking_id": "booking-1", "idempotency_key": "key-1"}}},
            5,
        ),
    ]
    execution = {
        "execution_id": "execution",
        "case_key": "control",
        "status": "passed",
        "attempt": 1,
        "case": {"content": {"turns": [{"user": "确认"}]}, "dsl": {"task_goal": goal}},
        "observed_events": events,
        "before": [],
        "after": [booking],
        "evidence_integrity": {"complete": True},
        "timing": {"faults": {"injected": 1}},
        "assertions": [
            {"id": 1, "evaluator_kind": "rule", "evaluator_version": "v1", "verdict": "passed"}
        ],
    }
    return execution, profile


def test_committed_recovery_requires_matching_original_lookup_and_sql(observed):
    e, p = observed
    assert describe(e, p)["outcome"] == "recovered"
    e["observed_events"] = e["observed_events"][:4]
    assert describe(e, p)["outcome"] == "committed_unverified"


@pytest.mark.parametrize("field", ["after", "before"])
def test_missing_sql_is_unknown_not_zero(observed, field):
    e, p = observed
    e[field] = None
    assert describe(e, p)["outcome"] == "unknown"
    assert not describe(e, p)["evidence_complete"]


def test_lookup_of_another_key_or_false_booking_does_not_prove_recovery(observed):
    e, p = observed
    e["observed_events"][-1]["payload"]["visible_result"]["data"]["booking_id"] = "not-real"
    assert describe(e, p)["outcome"] == "committed_unverified"
    e["observed_events"][-2]["payload"]["tool"] = "confirm_booking"
    e["observed_events"][-2]["payload"]["arguments"]["idempotency_key"] = "different"
    assert describe(e, p)["outcome"] == "violation"


@pytest.mark.parametrize("change", ["no_raw_data", "no_fault_sql", "hash", "sequence", "count"])
def test_corrupt_injection_receipts_are_unknown(observed, change):
    e, p = observed
    payload = e["observed_events"][2]["payload"]
    if change == "no_raw_data":
        payload["raw_result"]["data"] = None
    if change == "no_fault_sql":
        payload["after"] = None
    if change == "hash":
        p["config_hash"] = "broken"
    if change == "sequence":
        e["observed_events"][2]["run_seq"] = 1
    if change == "count":
        e["timing"]["faults"]["injected"] = 0
    assert fault_receipts(e, p)["state"] == "evidence_unknown"
    assert describe(e, p)["outcome"] == "unknown"


def test_scheduled_without_resolution_not_counted_as_not_triggered(observed):
    e, p = observed
    e["observed_events"] = [e["observed_events"][0], {"type": "fault.scheduled"}]
    e["timing"] = {}
    assert fault_receipts(e, p)["state"] == "evidence_unknown"
    assert describe(e, None)["outcome"] == "unknown"
    e["observed_events"].pop()
    assert describe(e, p)["outcome"] == "not_triggered"


def test_zero_write_is_only_degradation_candidate(observed):
    e, p = observed
    config = {**p["config"], "fault_type": "F01", "trigger_stage": "PRE_DISPATCH"}
    p = {"config": config, "config_hash": canonical_hash(config)}
    payload = e["observed_events"][2]["payload"]
    payload.update(
        profile_hash=p["config_hash"],
        fault_type="F01",
        stage="PRE_DISPATCH",
        before=[],
        after=[],
        raw_result=None,
        dispatched=False,
        visible_result={"commit_state": "not_committed"},
    )
    e["observed_events"][3]["payload"].update(
        raw_result=None, visible_result=deepcopy(payload["visible_result"])
    )
    e["observed_events"] = e["observed_events"][:4]
    e["after"] = []
    assert describe(e, p)["outcome"] == "degradation_candidate"


def test_goal_time_compares_instants_and_rejects_wrong_fields(observed):
    e, _ = observed
    goal = e["case"]["dsl"]["task_goal"]
    e["after"][0]["start_at"] = "2026-11-08T14:00:00+08:00"
    assert goal_matches(e["after"], goal)
    e["after"][0]["attendees"] = 9
    assert not goal_matches(e["after"], goal)


def test_cancelled_history_is_retained_without_false_goal_violation(observed):
    e, p = observed
    cancelled = {**e["after"][0], "status": "cancelled", "booking_id": "old", "request_key": "old"}
    e["after"].insert(0, cancelled)
    value = describe(e, p)
    assert value["outcome"] == "recovered" and value["goal_met"]
    assert len(value["sql_after"]) == 2
    e["after"] = [cancelled]
    assert describe(e, p)["outcome"] != "degradation_candidate"


def test_unknown_sql_status_excludes_recovery(observed):
    e, p = observed
    e["after"][0]["status"] = "invented"
    assert describe(e, p)["outcome"] == "unknown"


def test_missing_independent_goal_is_unknown_not_failure(observed):
    e, p = observed
    e["case"]["dsl"].pop("task_goal")
    assert describe(e, p)["outcome"] == "unknown"
    assert describe(e, p)["goal_met"] is None
    e["after"] = []
    assert describe(e, None)["outcome"] == "unknown"
