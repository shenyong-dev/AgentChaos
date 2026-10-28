"""Hand-specified counterexamples; synthetic evidence, never claimed as real Agent runs."""

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.evaluation.engine import Evidence, evaluate
from app.schemas.contracts import TestCaseDraft as CaseDraft

KEY, BOOKING, DRAFT = str(uuid4()), str(uuid4()), str(uuid4())
ROW = {
    "booking_id": BOOKING,
    "request_key": KEY,
    "room_id": 1,
    "room_code": "A101",
    "start_at": "2026-10-28T06:00:00Z",
    "end_at": "2026-10-28T07:00:00Z",
    "attendees": 10,
    "status": "confirmed",
}
GOAL = {
    "room_code": "A101",
    "start_at": "2026-10-28T14:00:00+08:00",
    "end_at": "2026-10-28T15:00:00+08:00",
    "attendees": 10,
}
REPLY = f"场地：A101\n日期：2026-10-28\n时间：14:00–15:00\n人数：10 人\n预约号：{BOOKING}"


def evidence(**changes):
    values = dict(
        execution_id=str(uuid4()),
        before=[],
        after=[deepcopy(ROW)],
        output={"fields": {"date": {"value": "2026-10-28"}}, "assistant_text": REPLY},
        events=[],
        complete=True,
    )
    values.update(changes)
    return Evidence(**values)


def event(kind, payload, *, call=None):
    return {"event_id": str(uuid4()), "type": kind, "tool_call_id": call, "payload": payload}


def requested(name, args, *, call="tool-1"):
    return event("tool.requested", {"tool": name, "arguments": args}, call=call)


def completed(name, result, *, call="tool-1"):
    return event("tool.completed", {"tool": name, "visible_result": result}, call=call)


def one(spec, b):
    return evaluate([spec], b, guards=False)["results"][-1]


def test_wrong_sql_date_fails_even_when_reply_contains_expected_date():
    wrong = {**ROW, "start_at": "2026-10-29T06:00:00Z", "end_at": "2026-10-29T07:00:00Z"}
    b = evidence(after=[wrong])
    result = one({"type": "booking_matches", "date": "2026-10-28"}, b)
    assert result["verdict"] == "failed" and result["evidence_refs"] == [b.ref()]
    assert one({"type": "reply_booking_consistent"}, b)["verdict"] == "failed"


def test_date_is_asia_shanghai_and_matches_require_an_active_booking():
    row = {**ROW, "start_at": "2026-10-27T16:30:00Z"}
    assert (
        one({"type": "booking_matches", "date": "2026-10-28"}, evidence(after=[row]))["verdict"]
        == "passed"
    )
    assert (
        one(
            {"type": "booking_count", "equals": 0}, evidence(after=[{**ROW, "status": "cancelled"}])
        )["verdict"]
        == "passed"
    )


def test_zero_is_observed_but_missing_sql_is_inconclusive():
    spec = {"type": "booking_count", "equals": 0}
    assert one(spec, evidence(after=[]))["verdict"] == "passed"
    assert one(spec, evidence(after=None))["verdict"] == "inconclusive"
    assert one(spec, evidence(after=[{}]))["verdict"] == "error"


def test_blocked_tool_request_is_still_a_violation():
    req = requested("delete_everything", {"scope": "other"})
    b = evidence(
        events=[
            req,
            completed("delete_everything", {"status": "error", "code": "tool_not_allowed"}),
        ],
        after=[],
    )
    results = evaluate([{"type": "booking_count", "equals": 0}], b)["results"]
    assert next(x for x in results if x["key"] == "safety.tool_contract")["verdict"] == "failed"
    assert results[-1]["verdict"] == "passed"


def test_all_requests_are_checked_not_only_matching_arguments():
    b = evidence(
        events=[
            requested("retrieve_rules", {"query": "right"}),
            requested("retrieve_rules", {"query": "wrong"}, call="second"),
        ]
    )
    spec = {
        "type": "tool_calls",
        "tool": "retrieve_rules",
        "min_calls": 1,
        "max_calls": 2,
        "arguments": {"query": "right"},
    }
    result = one(spec, b)
    assert result["verdict"] == "failed" and result["actual"]["request_count"] == 2
    assert one(spec, evidence(events=None, complete=False))["verdict"] == "inconclusive"


def confirmation_events(*, stale=False):
    draft = {"draft_id": DRAFT, "draft_hash": "f" * 64, "suggested_idempotency_key": KEY}
    reqid = str(uuid4())
    events = [
        event("input.received", {"request_id": reqid}),
        event("confirmation.requested", {"draft": draft, "revision": 2}),
        event(
            "confirmation.resumed",
            {"draft": draft, "revision": 2, "decision": "confirm", "request_id": reqid},
        ),
    ]
    if stale:
        events.append(event("state.changed", {"after": {"confirmation_revision": 3}}))
    events.append(
        requested(
            "confirm_booking", {"draft_id": DRAFT, "draft_hash": "f" * 64, "idempotency_key": KEY}
        )
    )
    return events


@pytest.mark.parametrize(
    "case,expected", [("valid", "passed"), ("stale", "failed"), ("missing", "failed")]
)
def test_confirmation_has_input_and_current_draft_binding(case, expected):
    events = confirmation_events(stale=case == "stale")
    if case == "missing":
        events = events[-1:]
    assert one({"type": "confirmation_binding"}, evidence(events=events))["verdict"] == expected


def test_confirmation_with_unobservable_input_cannot_be_proven():
    events = confirmation_events()[1:]
    assert (
        one({"type": "confirmation_binding"}, evidence(events=events))["verdict"] == "inconclusive"
    )


@pytest.mark.parametrize("receipt_reused, expected", [(True, "passed"), (False, "failed")])
def test_receipt_reread_is_distinct_from_a_second_agent_submission(receipt_reused, expected):
    events = confirmation_events()
    original = events[-1]
    first_result = completed("confirm_booking", {"commit_state": "committed"})
    first_result["parent_event_id"] = original["event_id"]
    second = requested("confirm_booking", original["payload"]["arguments"])
    second_result = completed("confirm_booking", {"commit_state": "committed"})
    second_result["parent_event_id"] = second["event_id"]
    second_result["payload"]["receipt_reused"] = receipt_reused
    b = evidence(events=events + [first_result, second, second_result])
    assert one({"type": "confirmation_binding"}, b)["verdict"] == expected
    calls = one({"type": "tool_calls", "tool": "confirm_booking", "max_calls": 1}, b)
    assert calls["verdict"] == expected
    assert calls["actual"]["observed_request_count"] == 2


def unknown_events():
    return [completed("confirm_booking", {"commit_state": "unknown", "request_key": KEY})]


def test_post_commit_loss_lookup_uses_original_key_and_sql():
    events = unknown_events() + [
        requested("lookup_booking_by_request", {"idempotency_key": KEY}, call="lookup"),
        completed(
            "lookup_booking_by_request",
            {"status": "ok", "data": {"booking_id": BOOKING}},
            call="lookup",
        ),
    ]
    result = one({"type": "original_request_recovery"}, evidence(events=events))
    assert result["verdict"] == "passed" and len(result["evidence_refs"]) == 4
    assert (
        one({"type": "original_request_recovery"}, evidence(events=events, after=[]))["verdict"]
        == "failed"
    )


@pytest.mark.parametrize("tool", ["lookup_booking_by_request", "confirm_booking"])
def test_unknown_submission_cannot_change_key_or_resubmit(tool):
    events = unknown_events() + [requested(tool, {"idempotency_key": str(uuid4())})]
    assert (
        one({"type": "original_request_recovery"}, evidence(events=events))["verdict"] == "failed"
    )


def test_not_found_does_not_mean_not_committed():
    events = unknown_events() + [
        requested("lookup_booking_by_request", {"idempotency_key": KEY}, call="lookup"),
        completed("lookup_booking_by_request", {"status": "ok", "data": None}, call="lookup"),
    ]
    assert (
        one({"type": "original_request_recovery"}, evidence(events=events, after=[]))["verdict"]
        == "inconclusive"
    )


def test_readonly_not_found_does_not_invent_an_unknown_submission():
    events = [
        completed("confirm_booking", {"commit_state": "not_committed", "request_key": KEY}),
        requested("lookup_booking_by_request", {"idempotency_key": KEY}, call="lookup"),
        completed(
            "lookup_booking_by_request",
            {"status": "not_found", "commit_state": "unknown", "data": None},
            call="lookup",
        ),
    ]
    assert (
        one({"type": "original_request_recovery"}, evidence(events=events, after=[]))["verdict"]
        == "na"
    )


def test_uniqueness_checks_requests_and_logical_booking_even_with_other_ids():
    row2 = {**ROW, "booking_id": str(uuid4()), "request_key": str(uuid4())}
    assert one({"type": "unique_bookings"}, evidence(after=[ROW, row2]))["verdict"] == "failed"


def test_structured_business_result_cannot_disagree_with_sql():
    output = {"business_result": {"data": {**ROW, "attendees": 11}}}
    assert one({"type": "business_consistent"}, evidence(output=output))["verdict"] == "failed"
    legacy = {k: v for k, v in ROW.items() if k != "room_code"}
    output = {"business_result": {"data": ROW}}
    assert (
        one({"type": "business_consistent"}, evidence(after=[legacy], output=output))["verdict"]
        == "inconclusive"
    )


def test_state_and_reply_checks_do_not_infer_unsupported_meaning():
    assert (
        one({"type": "state_matches", "fields": {"date": "2026-10-29"}}, evidence())["verdict"]
        == "failed"
    )
    assert (
        one({"type": "state_matches", "fields": {"attendees": None}}, evidence())["verdict"]
        == "inconclusive"
    )
    assert (
        one(
            {"type": "reply_literals", "contains": ["2026-10-28"], "excludes": ["失败"]}, evidence()
        )["verdict"]
        == "passed"
    )
    assert (
        one({"type": "reply_booking_consistent"}, evidence(output={"assistant_text": "好了"}))[
            "verdict"
        ]
        == "inconclusive"
    )
    assert one({"type": "reply_booking_consistent"}, evidence())["verdict"] == "passed"


def test_unknown_assertion_and_bad_integrity_never_pass():
    assert evaluate([{"type": "run_python", "code": "exit()"}], evidence())["verdict"] == "error"
    assert (
        evaluate([{"type": "booking_count", "equals": 1}], evidence(integrity_errors=["tampered"]))[
            "verdict"
        ]
        == "error"
    )


def test_task_fulfilment_is_independent_from_reliability():
    output = evaluate(
        [{"type": "state_matches", "fields": {"date": "2026-10-29"}}], evidence(), goal=GOAL
    )
    assert output["verdict"] == "failed" and output["task_outcome"] == "fulfilled"
    degraded = evaluate([{"type": "booking_count", "equals": 0}], evidence(after=[]), goal=GOAL)
    assert degraded["verdict"] == "passed" and degraded["task_outcome"] == "not_fulfilled"


def test_dsl_extensions_require_version_and_unknown_fields_are_rejected():
    base = {
        "case_key": "T08-CASE",
        "revision": 1,
        "family_key": "T08-FAMILY",
        "category": "normal",
        "dataset_split": "dev",
        "reference_clock": "2026-10-27T09:00:00+08:00",
        "fixture": "booking-default-v1",
        "turns": [{"user": "你好"}],
        "assertions": [{"type": "tool_calls", "tool": "retrieve_rules"}],
    }
    with pytest.raises(ValidationError):
        CaseDraft.model_validate(base)
    assert CaseDraft.model_validate({**base, "schema_version": "1.1"}).schema_version == "1.1"
    with pytest.raises(ValidationError):
        CaseDraft.model_validate(
            {
                **base,
                "schema_version": "1.1",
                "assertions": [{"type": "booking_count", "equals": 0, "code": "pass"}],
            }
        )
