"""Presentation partitions retain the original report denominators and evidence limits."""

from copy import deepcopy

from app.reliability.service import booking_count, dimension, projection


def entry(
    identifier="one",
    *,
    status="passed",
    verdict="passed",
    outcome="fulfilled",
    complete=True,
    mode="real_provider",
    split="development",
    metric="confirmation_binding",
):
    return {
        "execution_id": identifier,
        "case_key": identifier,
        "case_hash": identifier,
        "attempt": 1,
        "status": status,
        "gateway_mode": mode,
        "dataset_split": split,
        "usage": {},
        "evidence_integrity": {"complete": complete},
        "behavior": {"tool_requests": 1, "confirmation_events": 1, "write_requests": 1},
        "business": {"active_bookings": 1},
        "assertions": [
            {
                "id": 1,
                "metric": metric,
                "verdict": verdict,
                "evaluator_kind": "rule",
                "evaluator_version": "v1-input-one",
                "actual": {"task_outcome": outcome, "evaluator": {"version": "v1"}},
            }
        ],
    }


def test_waiting_cancelled_platform_error_and_missing_evidence_are_outside_business_denominators():
    rows = [
        entry(),
        entry("failed", status="failed", verdict="failed", outcome="not_fulfilled"),
        entry("safe", outcome="safely_degraded"),
        entry("wait", status="waiting_input"),
        entry("cancel", status="cancelled"),
        entry("error", status="error"),
        entry("missing", complete=False),
    ]
    result = projection(rows)[0]["statistics"]
    assert result["reliability"]["numerator"] == 2
    assert result["reliability"]["denominator"] == 3
    assert result["task_fulfilment"]["numerator"] == 1
    assert result["task_fulfilment"]["denominator"] == 2
    assert result["task_outcomes"]["safely_degraded"] == 1
    assert result["rule_outcomes"]["evidence_incomplete"] == 1
    assert result["rule_outcomes"]["excluded_error"] == 1


def test_sources_splits_and_evaluator_code_are_never_pooled_as_one_score():
    other = entry("other")
    other["assertions"][0]["actual"]["evaluator"]["version"] = "v2"
    groups = projection(
        [
            entry(),
            entry("offline", mode="offline_test_double"),
            entry("holdout", split="holdout"),
            other,
        ]
    )
    assert len(groups) == 4
    assert all(group["statistics"]["planned_executions"] == 1 for group in groups)


def test_guards_without_an_observed_confirmation_or_booking_do_not_claim_coverage():
    value = entry()
    value["behavior"] = {"tool_requests": 0, "confirmation_events": 0, "write_requests": 0}
    value["business"]["active_bookings"] = 0
    assert dimension(value, "confirmation", {"confirmation_binding"})["status"] == "untested"
    value["assertions"][0]["metric"] = "unique_bookings"
    assert dimension(value, "uniqueness", {"unique_bookings"})["status"] == "untested"
    value["assertions"][0]["metric"] = "original_request_recovery"
    value["assertions"][0]["verdict"] = "na"
    assert dimension(value, "recovery", {"original_request_recovery"})["status"] == "untested"


def test_latest_saved_version_controls_coverage_but_unknown_evidence_cannot_turn_green():
    value = entry()
    old = deepcopy(value["assertions"][0])
    value["assertions"][0].update(id=2, evaluator_version="v1-input-two", verdict="failed")
    value["assertions"].append(old)
    assert dimension(value, "confirmation", {"confirmation_binding"})["status"] == "failed"
    value["evidence_integrity"]["complete"] = False
    assert dimension(value, "confirmation", {"confirmation_binding"})["status"] == "inconclusive"


def test_repeated_executions_remain_samples_with_overlapping_dimensions_not_independent_cases():
    first, second = entry(), entry("two")
    second["case_hash"] = first["case_hash"]
    groups = projection([first, second])
    assert groups[0]["statistics"]["unique_cases"] == 1
    assert groups[0]["statistics"]["planned_executions"] == 2
    assert groups[0]["dimensions"][1]["denominator"] == 2


def test_missing_or_partial_sql_is_unknown_but_a_valid_empty_snapshot_is_zero():
    assert booking_count(None) is None
    assert booking_count({}) is None
    assert booking_count([{"status": "confirmed"}]) is None
    assert booking_count([]) == 0


def test_proving_a_tool_absent_does_not_cover_actual_parameter_consistency():
    value = entry(metric="tool_calls")
    row = value["assertions"][0]
    row["expected"] = {"tool": "confirm_booking", "min_calls": 0, "max_calls": 0}
    row["actual"]["value"] = {"request_count": 0}
    assert dimension(value, "parameters", {"tool_calls"})["status"] == "untested"
    row["expected"]["arguments"] = {"attendees": 8}
    value["behavior"]["tool_counts"] = {"confirm_booking": 1}
    assert dimension(value, "parameters", {"tool_calls"})["status"] == "passed"
