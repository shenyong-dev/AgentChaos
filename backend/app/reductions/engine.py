"""Bounded deletion proposals and a strict SQL failure identity, never an LLM verdict."""

from copy import deepcopy

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.reports.analysis import latest_rows

TRIAL_SOURCE = "reduction_trial:T17M"
REPEATS = 3
MAX_CANDIDATES = 2


def input_size(turns):
    return {"turns": len(turns), "utf8_bytes": sum(len(t["user"].encode()) for t in turns)}


def proposals(turns):
    """Start with largest deletions; fixed bounded search, not a shortest-path claim."""
    count = len(turns)
    if count < 2:
        return []
    # Users may choose another deletion set before it is frozen. No word rewriting.
    options = [list(range(1, count)), list(range(1, count - 1)) if count > 2 else [0]]
    result = []
    for keep in options:
        if keep not in [item["keep"] for item in result]:
            result.append({"keep": keep, "turns": [deepcopy(turns[i]) for i in keep]})
    return result[:MAX_CANDIDATES]


def subset(turns, keep):
    if not keep or keep != sorted(set(keep)) or len(keep) >= len(turns):
        raise AgentError("reduction_not_smaller", "只能按原顺序删除完整轮次，须至少保留一轮。", 400)
    if keep[0] < 0 or keep[-1] >= len(turns):
        raise AgentError("reduction_index_invalid", "保留轮次超出原对话范围。", 400)
    return [deepcopy(turns[i]) for i in keep]


def signature(execution):
    """V1 supports count invariants with complete SQL, object fields and rule inputs."""
    if (
        execution.get("status") != "failed"
        or not execution.get("evidence_integrity", {}).get("complete")
        or execution.get("external_user_inputs")
        or execution.get("attempt") != 1
        or not isinstance(execution.get("after"), list)
        or not isinstance(execution.get("before"), list)
    ):
        return None
    rows = [
        r
        for r in latest_rows(execution.get("assertions", []))
        if r.get("metric") == "booking_count"
        and r.get("evaluator_kind") == "rule"
        and r.get("verdict") == "failed"
        and r.get("expected", {}).get("type") == "booking_count"
    ]
    if len(rows) != 1:
        return None
    row = rows[0]
    actual = row.get("actual", {})
    count = actual.get("value", {}).get("active_count")
    expected_count = row["expected"].get("equals")
    bookings = execution["after"]
    fields = ("room_code", "start_at", "end_at", "attendees", "status")
    if (
        type(count) is not int
        or type(expected_count) is not int
        or count == expected_count
        or count != sum(b.get("status") == "confirmed" for b in bookings)
    ):
        return None
    if not actual.get("evaluator") or not actual.get("input_hash") or not row.get("evidence_refs"):
        return None
    if any(any(key not in b for key in fields) for b in bookings + execution["before"]):
        return None
    value = {
        "invariant": "booking_count",
        "metric": row["metric"],
        "expected": row["expected"],
        "actual_count": count,
        "evaluator": actual["evaluator"],
        "before": sorted(
            [{k: b[k] for k in fields} for b in execution["before"]], key=canonical_hash
        ),
        "after": sorted([{k: b[k] for k in fields} for b in bookings], key=canonical_hash),
    }
    return {"value": value, "hash": canonical_hash(value), "assertion_id": row["id"]}


def repeat_summary(expected, observations):
    counts = {"same_failure": 0, "different_failure": 0, "not_reproduced": 0, "unproven": 0}
    details = []
    for item in observations:
        found = signature(item)
        state = (
            "same_failure"
            if found and found["hash"] == expected["hash"]
            else "different_failure"
            if found
            else "not_reproduced"
            if item.get("status") == "passed"
            and item.get("evidence_integrity", {}).get("complete")
            and item.get("attempt") == 1
            and not item.get("external_user_inputs")
            else "unproven"
        )
        counts[state] += 1
        details.append({"execution_id": item.get("execution_id"), "outcome": state})
    valid = counts["same_failure"] + counts["different_failure"] + counts["not_reproduced"]
    return {
        **counts,
        "valid": valid,
        "planned": REPEATS,
        "observed": len(observations),
        "verified": len(observations) == REPEATS and counts["same_failure"] == REPEATS,
        "rate": counts["same_failure"] / valid if valid else None,
        "details": details,
    }


def conditions(report, execution):
    manifest = report["run"]["manifest"]
    content = execution["case"]["content"]
    return {
        **{
            key: manifest.get(key)
            for key in (
                "target_version_id",
                "target",
                "limits",
                "fault_profile",
                "roles",
                "pricing",
                "evaluation",
                "trace_schema",
                "relation_comparator",
            )
        },
        "budget": report["run"]["budget"],
        "seed": execution["seed"],
        "expected": content["expected"],
        "fixture": content["fixture"],
        "gateway_mode": execution["gateway_mode"],
    }


def usage_metrics(observations):
    tickets = [
        t for item in observations for t in (item.get("usage") or {}).get("tickets", {}).values()
    ]
    settled = [
        t for t in tickets if t.get("state") == "settled" and isinstance(t.get("usage"), dict)
    ]
    unknown = len(tickets) - len(settled)
    known_input = sum(t["usage"]["input_tokens"] for t in settled)
    known_output = sum(t["usage"]["output_tokens"] for t in settled)
    return {
        "observed_calls": len(tickets),
        "unknown_calls": unknown,
        "known_input_tokens": known_input,
        "known_output_tokens": known_output,
        "input_tokens": known_input if tickets and not unknown else None,
        "estimated_microusd": sum(t.get("estimate_microusd", 0) for t in settled)
        if tickets and not unknown
        else None,
        "unknown_reservation_microusd": sum(
            t.get("upper_microusd", 0) for t in tickets if t not in settled
        ),
    }
