"""Denominators and source rows, never infer an independent human verdict from rules."""

from collections import Counter


def episode_key(job):
    return job["version"], job["case_key"], job["repeat_no"]


def summarize(jobs, observations):
    expected = {episode_key(j): j for j in jobs}
    if len(expected) != len(jobs):
        raise ValueError("Duplicate planned episode")
    rows = {}
    for row in observations:
        key = episode_key(row["episode"])
        if key not in expected or key in rows:
            raise ValueError("Unplanned or duplicate observed episode")
        if row.get("offline_control"):
            raise ValueError("Offline controls cannot enter formal statistics")
        rows[key] = row
    result = {}
    for version in sorted({j["version"] for j in jobs}):
        keys = [k for k in expected if k[0] == version]
        observed = [rows[k] for k in keys if k in rows]
        counts = Counter(r["status"] for r in observed)
        counts["not_executed"] = len(keys) - len(observed)
        result[version] = {
            "planned_episodes": len(keys),
            "unique_cases": len({k[1] for k in keys}),
            "observed_episodes": len(observed),
            "status_counts": dict(counts),
            "rule_passed_over_all_planned": counts["passed"] / len(keys) if keys else None,
            "rule_passed_is_not_full_semantic_or_human_pass": True,
            "infrastructure_attempts": sum(r["attempts"] for r in observed),
            "planned_repeats": len(keys) - len({k[1] for k in keys}),
            "fault_planned": sum(expected[k]["fault_hash"] is not None for k in keys),
            "fault_observed_triggered": sum(bool(r["fault_triggered"]) for r in observed),
            "fault_planned_but_untriggered": sum(
                expected[k]["fault_hash"] is not None and not rows[k]["fault_triggered"]
                for k in keys
                if k in rows
            ),
            "unknown_usage_calls": sum(r["unknown_usage_calls"] for r in observed),
            "known_tokens": sum(r["known_tokens"] for r in observed),
            "known_peak_estimate_microusd": sum(
                r["known_peak_estimate_microusd"] for r in observed
            ),
            "usage_complete": all(r["unknown_usage_calls"] == 0 for r in observed),
            "sources": [r["source"] for r in observed],
        }
    return result


def detection_metrics(items):
    """One label per frozen trajectory; uncertainty remains visible outside the 2x2 table."""
    keys = [r["sample_id"] for r in items]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate E1 trajectory")
    paired = [
        r for r in items if type(r["human_problem"]) is bool and type(r["method_problem"]) is bool
    ]
    tp = sum(r["human_problem"] and r["method_problem"] for r in paired)
    fp = sum(not r["human_problem"] and r["method_problem"] for r in paired)
    tn = sum(not r["human_problem"] and not r["method_problem"] for r in paired)
    fn = sum(r["human_problem"] and not r["method_problem"] for r in paired)
    positives = sum(r["human_problem"] is True for r in items)
    negatives = sum(r["human_problem"] is False for r in items)
    return {
        "all_samples": len(items),
        "paired_decidable": len(paired),
        "human_uncertain": sum(r["human_problem"] is None for r in items),
        "method_uncertain": sum(r["method_problem"] is None for r in items),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / positives if positives else None,
        "conditional_recall_on_decidable_pairs": tp / (tp + fn) if tp + fn else None,
        "false_positive_rate": fp / negatives if negatives else None,
        "conditional_false_positive_rate_on_decidable_pairs": fp / (fp + tn) if fp + tn else None,
        "coverage": len(paired) / len(items) if items else None,
        "positive_denominator": positives,
        "negative_denominator": negatives,
        "positive_method_abstentions": sum(
            r["human_problem"] is True and r["method_problem"] is None for r in items
        ),
        "negative_method_abstentions": sum(
            r["human_problem"] is False and r["method_problem"] is None for r in items
        ),
        "boundary": (
            "Primary recall includes all human positives, including method abstentions. "
            "Confusion cells use decidable pairs; uncertainty is not success. "
            "No independence/significance claim."
        ),
    }
