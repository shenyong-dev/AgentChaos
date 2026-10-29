from copy import deepcopy

import pytest

from experiments.analysis import detection_metrics, summarize


def test_missing_error_untriggered_repeat_and_unknown_usage_stay_in_denominators():
    jobs = [
        {"version": "V0", "case_key": "a", "repeat_no": n, "fault_hash": "fault"} for n in [1, 2, 3]
    ]
    row = {
        "episode": jobs[0],
        "status": "error",
        "attempts": 2,
        "fault_triggered": False,
        "unknown_usage_calls": 1,
        "known_tokens": 10,
        "known_peak_estimate_microusd": 2,
        "source": "raw.json",
    }
    value = summarize(jobs, [row])["V0"]
    assert value["planned_episodes"] == 3 and value["unique_cases"] == 1
    assert value["status_counts"] == {"error": 1, "not_executed": 2}
    assert value["infrastructure_attempts"] == 2 and value["planned_repeats"] == 2
    assert value["fault_planned_but_untriggered"] == 1 and value["fault_observed_triggered"] == 0
    assert value["rule_passed_over_all_planned"] == 0 and value["usage_complete"] is False
    with pytest.raises(ValueError, match="duplicate"):
        summarize(jobs, [row, row])
    control = deepcopy(row)
    control["offline_control"] = True
    with pytest.raises(ValueError, match="Offline"):
        summarize(jobs, [control])


def test_no_positive_denominator_is_na_and_uncertain_is_not_true_negative():
    data = [
        {"sample_id": "a", "human_problem": False, "method_problem": False},
        {"sample_id": "b", "human_problem": False, "method_problem": None},
        {"sample_id": "c", "human_problem": None, "method_problem": True},
    ]
    value = detection_metrics(data)
    assert value["tn"] == 1 and value["paired_decidable"] == 1
    assert value["recall"] is None and value["precision"] is None
    assert value["human_uncertain"] == value["method_uncertain"] == 1
    assert value["coverage"] == 1 / 3


def test_primary_recall_keeps_method_abstentions_in_human_positive_denominator():
    value = detection_metrics(
        [
            {"sample_id": "a", "human_problem": True, "method_problem": True},
            {"sample_id": "b", "human_problem": True, "method_problem": None},
        ]
    )
    assert value["recall"] == 0.5 and value["positive_denominator"] == 2
    assert value["positive_method_abstentions"] == 1
    assert value["conditional_recall_on_decidable_pairs"] == 1
