"""Failure identity: missing evidence, altered outcomes and interrupted runs are not proofs."""

from copy import deepcopy

import pytest

from app.agents.errors import AgentError
from app.reductions.engine import proposals, repeat_summary, signature, subset, usage_metrics


def execution():
    return {
        "execution_id": "controlled",
        "status": "failed",
        "attempt": 1,
        "evidence_integrity": {"complete": True},
        "external_user_inputs": [],
        "before": [],
        "after": [
            {
                "room_code": "A101",
                "start_at": "2026-11-07T06:00:00+00:00",
                "end_at": "2026-11-07T07:00:00+00:00",
                "attendees": 8,
                "status": "confirmed",
            }
        ],
        "assertions": [
            {
                "id": 1,
                "key": "count",
                "metric": "booking_count",
                "verdict": "failed",
                "evaluator_kind": "rule",
                "evaluator_version": "frozen",
                "expected": {"type": "booking_count", "equals": 0},
                "actual": {
                    "value": {"active_count": 1},
                    "evaluator": {"version": "frozen"},
                    "input_hash": "bound",
                },
                "evidence_refs": ["snapshot:controlled:after"],
            }
        ],
    }


def test_repeats_require_all_three_and_identical_business_objects():
    original = execution()
    expected = signature(original)
    assert repeat_summary(expected, [original] * 3)["verified"]
    assert not repeat_summary(expected, [original] * 2)["verified"]
    changed = deepcopy(original)
    changed["after"][0]["attendees"] = 10
    summary = repeat_summary(expected, [original, changed, {"status": "cancelled"}])
    assert not summary["verified"] and summary["same_failure"] == 1
    assert summary["different_failure"] == 1 and summary["unproven"] == 1
    assert summary["rate"] == 0.5


@pytest.mark.parametrize(
    "change",
    [
        "missing_sql",
        "mismatched_count",
        "external_input",
        "retry",
        "missing_rule_input",
        "missing_object",
    ],
)
def test_unproven_evidence_never_counts_as_same_failure(change):
    value = execution()
    if change == "missing_sql":
        value["after"] = None
    elif change == "mismatched_count":
        value["assertions"][0]["actual"]["value"]["active_count"] = 0
    elif change == "external_input":
        value["external_user_inputs"] = ["确认"]
    elif change == "retry":
        value["attempt"] = 2
    elif change == "missing_rule_input":
        value["assertions"][0]["actual"].pop("input_hash")
    else:
        value["after"][0].pop("attendees")
    assert signature(value) is None


def test_random_nonreproduction_and_budget_interrupt_are_separate():
    original = execution()
    passed = deepcopy(original)
    passed.update(status="passed", after=[])
    summary = repeat_summary(
        signature(original),
        [original, passed, {"status": "error", "error": {"code": "model_budget_exhausted"}}],
    )
    assert (summary["same_failure"], summary["not_reproduced"], summary["unproven"]) == (1, 1, 1)
    assert not summary["verified"]
    passed["attempt"] = 2
    assert repeat_summary(signature(original), [passed])["unproven"] == 1


def test_proposals_never_rewrite_words_and_reject_invalid_deletions():
    turns = [{"user": t} for t in ["你好", "仅查看草稿", "拒绝确认", "谢谢"]]
    candidates = proposals(turns)
    assert [c["keep"] for c in candidates] == [[1, 2, 3], [1, 2]]
    for candidate in candidates:
        assert candidate["turns"] == subset(turns, candidate["keep"])
    for keep in [[], [2, 1], [1, 1], [-1], [4], [0, 1, 2, 3]]:
        with pytest.raises(AgentError):
            subset(turns, keep)


def test_unknown_usage_is_preserved_instead_of_zero_cost():
    known = {
        "state": "settled",
        "usage": {"input_tokens": 123, "output_tokens": 5},
        "estimate_microusd": 10,
    }
    unknown = {"state": "unknown", "upper_microusd": 100}
    value = usage_metrics([{"usage": {"tickets": {"known": known, "unknown": unknown}}}])
    assert value["input_tokens"] is None and value["estimated_microusd"] is None
    assert value["known_input_tokens"] == 123 and value["unknown_reservation_microusd"] == 100
    assert usage_metrics([])["estimated_microusd"] is None
