"""Observed fault pairs, with independent business facts and no inferred causal score."""

import json
from collections import Counter

from app.agents.versions import canonical_hash
from app.reductions.engine import usage_metrics
from app.reports.analysis import compare, latest_rows, verdict

FIELDS = ("room_code", "start_at", "end_at", "attendees", "status")


def normalized(rows):
    if not isinstance(rows, list) or any(
        not isinstance(r, dict)
        or any(k not in r for k in FIELDS)
        or r.get("status") not in {"confirmed", "cancelled"}
        for r in rows
    ):
        return None
    return sorted(({k: r[k] for k in FIELDS} for r in rows), key=canonical_hash)


def goal_matches(rows, goal):
    rows = [r for r in rows if r.get("status") == "confirmed"]
    if not goal or len(rows) != 1:
        return False
    from datetime import datetime

    row = rows[0]
    try:
        return (
            row["status"] == "confirmed"
            and row["room_code"] == goal["room_code"]
            and row["attendees"] == goal["attendees"]
            and datetime.fromisoformat(row["start_at"]) == datetime.fromisoformat(goal["start_at"])
            and datetime.fromisoformat(row["end_at"]) == datetime.fromisoformat(goal["end_at"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def fault_receipts(execution, frozen):
    events = execution.get("observed_events", [])
    resolved = [e for e in events if e["type"] in {"fault.injected", "fault.not_applied"}]
    if not frozen:
        return {
            "state": "normal"
            if not any(e["type"].startswith("fault.") for e in events)
            else "evidence_unknown",
            "receipts": resolved,
        }
    config = frozen.get("config", {})
    if canonical_hash(config) != frozen.get("config_hash"):
        return {"state": "evidence_unknown", "receipts": resolved}
    injected = [e for e in resolved if e["type"] == "fault.injected"]
    saved = (execution.get("timing") or {}).get("faults", {})
    if not resolved:
        state = (
            "not_reached"
            if execution.get("evidence_integrity", {}).get("complete")
            and not saved.get("reserved")
            and not any(e["type"] == "fault.scheduled" for e in events)
            else "evidence_unknown"
        )
        return {"state": state, "receipts": []}
    if len(injected) != saved.get("injected", 0) or len(injected) > config.get("max_triggers", 0):
        return {"state": "evidence_unknown", "receipts": resolved}
    valid = True
    for e in resolved:
        p = e["payload"]
        request = next(
            (
                r
                for r in events
                if r["event_id"] == e.get("parent_event_id") and r["type"] == "tool.requested"
            ),
            None,
        )
        returned = next(
            (
                r
                for r in events
                if r["type"] in {"tool.completed", "tool.error"}
                and r.get("parent_event_id") == e.get("parent_event_id")
                and r["payload"].get("fault_event_id") == e["event_id"]
            ),
            None,
        )
        valid &= bool(
            request
            and returned
            and request.get("tool_call_id") == e.get("tool_call_id")
            and request["payload"].get("tool") == config.get("target_tool")
            and p.get("profile_hash") == frozen["config_hash"]
            and p.get("fault_type") == config.get("fault_type")
            and p.get("stage") == config.get("trigger_stage")
            and request["run_seq"] < e["run_seq"] < returned["run_seq"]
            and returned["payload"].get("visible_result") == p.get("visible_result")
            and returned["payload"].get("raw_result") == p.get("raw_result")
        )
        if e["type"] != "fault.injected":
            continue
        raw, visible = p.get("raw_result"), p.get("visible_result")
        if config.get("fault_type") == "F01":
            valid &= (
                raw is None
                and p.get("dispatched") is False
                and isinstance(visible, dict)
                and visible.get("commit_state") == "not_committed"
                and isinstance(p.get("before"), list)
                and p.get("before") == p.get("after")
            )
        elif config.get("fault_type") == "F03":
            try:
                json.loads(visible)
                corrupt = False
            except (TypeError, ValueError):
                corrupt = isinstance(visible, str)
            valid &= isinstance(raw, dict) and corrupt and p.get("dispatched") is True
        elif config.get("fault_type") == "F04":
            valid &= (
                isinstance(raw, dict)
                and raw.get("commit_state") == "committed"
                and isinstance(raw.get("data"), dict)
                and isinstance(p.get("after"), list)
                and isinstance(visible, dict)
                and visible.get("commit_state") == "unknown"
                and visible.get("request_key")
                == ((request or {}).get("payload", {}).get("arguments") or {}).get(
                    "idempotency_key"
                )
                and any(
                    b.get("booking_id") == raw.get("data", {}).get("booking_id")
                    and b.get("request_key") == visible.get("request_key")
                    for b in p.get("after", [])
                )
            )
        else:
            valid = False
    return {
        "state": "triggered"
        if valid and injected
        else "stage_not_applied"
        if valid
        else "evidence_unknown",
        "receipts": resolved,
    }


def describe(execution, profile):
    events = execution.get("observed_events", [])
    requests = [e for e in events if e["type"] == "tool.requested"]
    fault = fault_receipts(execution, profile)
    lost = [
        e
        for e in fault["receipts"]
        if e["type"] == "fault.injected" and e["payload"].get("fault_type") == "F04"
    ]
    lookups, retries, changed_keys = [], [], []
    for lost_event in lost:
        visible = lost_event["payload"].get("visible_result")
        key = visible.get("request_key") if isinstance(visible, dict) else None
        if not key:
            continue
        for request in requests:
            if request["run_seq"] <= lost_event["run_seq"]:
                continue
            tool = request["payload"].get("tool")
            given = (request["payload"].get("arguments") or {}).get("idempotency_key")
            if tool == "confirm_booking":
                (retries if given == key else changed_keys).append(request["event_id"])
            if tool != "lookup_booking_by_request" or given != key:
                continue
            result = next(
                (
                    e
                    for e in events
                    if e["type"] in {"tool.completed", "tool.error"}
                    and e.get("parent_event_id") == request["event_id"]
                ),
                None,
            )
            data = (result or {}).get("payload", {}).get("visible_result")
            booking = data.get("data") if isinstance(data, dict) else None
            if (
                isinstance(booking, dict)
                and booking.get("idempotency_key") == key
                and any(
                    b.get("booking_id") == booking.get("booking_id") and b.get("request_key") == key
                    for b in execution.get("after") or []
                )
            ):
                lookups.append(request["event_id"])
    rows = normalized(execution.get("after"))
    rules = latest_rows(execution.get("assertions", []))
    rule = verdict(rules)
    inputs = [e["payload"].get("text") for e in events if e["type"] == "input.received"]
    turns = execution["case"]["content"]["turns"]
    complete = (
        execution.get("evidence_integrity", {}).get("complete")
        and rows is not None
        and normalized(execution.get("before")) is not None
        and execution.get("attempt") == 1
        and not execution.get("external_user_inputs")
        and inputs == [t["user"] for t in turns]
        and execution.get("status") in {"passed", "failed", "inconclusive"}
        and rules
    )
    goal = execution["case"]["dsl"].get("task_goal")
    matches = goal_matches(rows or [], goal) if goal else None
    outcome = "unknown"
    if complete:
        if not profile and fault["state"] != "normal":
            outcome = "unknown"
        elif profile and fault["state"] != "triggered":
            outcome = (
                "not_triggered"
                if fault["state"] in {"not_reached", "stage_not_applied"}
                else "unknown"
            )
        elif rule == "failed" or changed_keys or rows and matches is False:
            outcome = "violation"
        elif rule == "passed" and goal:
            if not profile:
                outcome = "normal_success" if matches else "normal_not_fulfilled"
            elif matches:
                outcome = (
                    "recovered" if not lost or len(lookups) >= len(lost) else "committed_unverified"
                )
            elif not rows and execution.get("before") == []:
                outcome = "degradation_candidate"
    return {
        "execution_id": execution["execution_id"],
        "case_key": execution["case_key"],
        "status": execution["status"],
        "gateway_mode": execution.get("gateway_mode"),
        "evidence_complete": bool(complete),
        "outcome": outcome,
        "fault": fault,
        "goal_met": matches if rows is not None else None,
        "sql_before": execution.get("before"),
        "sql_after": execution.get("after"),
        "rule_verdict": rule,
        "assertions": execution.get("assertions", []),
        "original_request_lookups": lookups,
        "same_key_retries": retries,
        "different_key_attempts": changed_keys,
        "actions": [
            {
                "event_id": e["event_id"],
                "order": e["run_seq"],
                "tool": e["payload"].get("tool"),
                "arguments": e["payload"].get("arguments"),
            }
            for e in requests
        ],
        "final_answer": (execution.get("output") or {}).get("assistant_text"),
        "usage": usage_metrics([execution]),
        "timing": execution.get("timing"),
    }


def compare_faults(normal, fault):
    checks = [c for c in compare(normal, fault)["checks"] if c["condition"] != "fault"]
    for key in ("target", "roles", "evaluation"):
        a, b = normal["run"]["manifest"].get(key), fault["run"]["manifest"].get(key)
        checks.append(
            {
                "condition": "frozen_" + key,
                "status": "match" if a and a == b else "mismatch",
                "left": a,
                "right": b,
            }
        )
    profile = fault["run"]["manifest"].get("fault_profile")
    declaration = normal["run"]["manifest"].get("fault_profile") is None and bool(profile)
    checks.append(
        {
            "condition": "only_declared_fault",
            "status": "match" if declaration else "mismatch",
            "left": normal["run"]["manifest"].get("fault_profile"),
            "right": profile,
        }
    )
    paired, used = [], set()
    for a in normal["executions"]:
        peers = [
            b
            for b in fault["executions"]
            if (a["case_hash"], a["repeat_no"]) == (b["case_hash"], b["repeat_no"])
        ]
        if len(peers) != 1:
            continue
        b = peers[0]
        used.add(b["execution_id"])
        isolated = bool(
            a.get("scope_id")
            and b.get("scope_id")
            and a.get("session_id")
            and b.get("session_id")
            and a["scope_id"] != b["scope_id"]
            and a["session_id"] != b["session_id"]
        )
        initial = normalized(a.get("before")) is not None and normalized(
            a.get("before")
        ) == normalized(b.get("before"))
        pair_checks = {"independent_scope_session": isolated, "same_initial_business": initial}
        comparable = all(c["status"] == "match" for c in checks) and all(pair_checks.values())
        before, after = describe(a, None), describe(b, profile)
        paired.append(
            {
                "case_key": a["case_key"],
                "repeat_no": a["repeat_no"],
                "comparable": comparable,
                "checks": pair_checks,
                "normal": before,
                "fault": after,
                "complete_pair": comparable
                and before["evidence_complete"]
                and after["evidence_complete"],
                "protocol": {
                    "normal": normal["run"]["manifest"]
                    .get("workflow", {})
                    .get("paired_experiment"),
                    "fault": fault["run"]["manifest"].get("workflow", {}).get("paired_experiment"),
                },
            }
        )
    comparable = (
        bool(paired)
        and len(paired) == len(normal["executions"]) == len(fault["executions"])
        and all(p["comparable"] for p in paired)
    )
    outcomes = Counter(p["fault"]["outcome"] if p["complete_pair"] else "excluded" for p in paired)
    return {
        "schema": "fault-pair-v1",
        "normal_run": normal["run"]["run_id"],
        "fault_run": fault["run"]["run_id"],
        "conditions_match": comparable,
        "checks": checks,
        "pairs": paired,
        "summary": {
            "paired": len(paired),
            "complete": sum(bool(p["complete_pair"]) for p in paired),
            "unpaired": len(normal["executions"]) + len(fault["executions"]) - 2 * len(paired),
            "triggered": sum(p["fault"]["fault"]["state"] == "triggered" for p in paired),
            "complete_triggered": sum(
                p["complete_pair"] and p["fault"]["fault"]["state"] == "triggered" for p in paired
            ),
            "outcomes": dict(outcomes),
        },
        "boundary": (
            "故障是声明变量。配对不证明因果、统计显著或模型确定性；"
            "0写入降级候选仍需人工判断回答语义。"
        ),
        "run_elapsed_seconds": {"normal": run_elapsed(normal), "fault": run_elapsed(fault)},
        "read_only": True,
    }


def run_elapsed(report):
    """Whole run wall time, not per-model latency or P95."""
    from datetime import datetime

    try:
        start = datetime.fromisoformat(report["run"]["started_at"])
        end = datetime.fromisoformat(report["run"]["finished_at"])
        seconds = (end - start).total_seconds()
        return seconds if seconds >= 0 else None
    except (KeyError, TypeError, ValueError):
        return None
