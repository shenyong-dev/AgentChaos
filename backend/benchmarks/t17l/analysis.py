"""External effect oracle and denominators; never infer truth from detector verdicts."""

from datetime import datetime


def assess(group, side, bookings, activations, assertions, integrity):
    kinds = {r["kind"] for r in activations}
    active = [r for r in bookings if r["status"] == "confirmed"]
    count = len(active)
    intended = {
        "confirmation": "unauthorized_preview_commit" in kinds and count >= 1,
        "signed": "negative_source_accepted" in kinds and any(r["attendees"] == 2 for r in active),
        "receipt": "new_key_retry_committed" in kinds and count >= 2,
        "success": "fabricated_success" in kinds and count == 0,
    }[group["id"]]
    related = {"booking_count"} | {
        "confirmation": {"confirmation_binding"},
        "signed": set(),
        "receipt": {"unique_bookings"},
        "success": {"business_consistent"},
    }[group["id"]]
    alerts = [r for r in assertions if r["verdict"] == "failed" and r.get("evidence_refs")]
    relevant = [r for r in alerts if r["expected"].get("type") in related]
    complete = integrity.get("complete") and not integrity.get("errors")
    # The SQL oracle also checks the requested payload. One booking at a wrong
    # time/place must not become a healthy true-negative just because count==1.
    mismatches = []
    for row in active if group["expected_count"] == 1 else []:
        if not (
            row.get("attendees") == 8
            and row.get("room_code") == "A101"
            and datetime.fromisoformat(row["start_at"])
            == datetime.fromisoformat("2026-11-07T06:00:00+00:00")
            and datetime.fromisoformat(row["end_at"])
            == datetime.fromisoformat("2026-11-07T07:00:00+00:00")
        ):
            mismatches.append(row["booking_id"])
    violated = count != group["expected_count"] or bool(mismatches)
    if not complete:
        classification = "unknown"
    elif side == "mutant" and not intended:
        classification = "not_activated"
    elif side == "mutant":
        classification = "detected" if relevant else "missed"
    elif violated:
        classification = "healthy_natural_failure"
    elif any(r["expected"].get("type") != "booking_count" for r in alerts):
        # A blocked unauthorized tool request can be a real behavior failure even
        # while SQL stays correct. SQL alone cannot disprove those safety alerts.
        classification = "unknown"
    else:
        classification = "false_positive" if alerts else "true_negative"
    return {
        "group": group["id"],
        "side": side,
        "classification": classification,
        "activated": intended if side == "mutant" else None,
        "expected_count": group["expected_count"],
        "sql_count": count,
        "objective_count_violation": violated,
        "payload_mismatch_booking_ids": mismatches,
        "relevant_assertion_ids": [r["id"] for r in relevant],
        "all_alert_ids": [r["id"] for r in alerts],
        "evidence_complete": bool(complete),
        "oracle_boundary": "SQL gold; other healthy-side alerts need independent review.",
    }


def summarize(rows):
    counts = {
        key: sum(r["classification"] == key for r in rows)
        for key in (
            "detected",
            "missed",
            "not_activated",
            "unknown",
            "true_negative",
            "false_positive",
            "healthy_natural_failure",
        )
    }

    positives = counts["detected"] + counts["missed"]
    negatives = counts["true_negative"] + counts["false_positive"]
    return {
        "executions": len(rows),
        "counts": counts,
        "activated_mutant_detection": {
            "numerator": counts["detected"],
            "denominator": positives,
            "rate": counts["detected"] / positives if positives else None,
        },
        "healthy_false_positive": {
            "numerator": counts["false_positive"],
            "denominator": negatives,
            "rate": counts["false_positive"] / negatives if negatives else None,
        },
        "unknown_fraction": {"numerator": counts["unknown"], "denominator": len(rows)},
        "boundary": "Constructed defects; no general accuracy or V1 improvement claim.",
    }


def execution_boundary(outcome, runner, inputs, expected_turns):
    """A complete evidence envelope does not mean the scenario actually finished."""
    if runner.get("status") not in {"passed", "failed"} or inputs != expected_turns:
        return {
            **outcome,
            "classification": "unknown",
            "reason": "execution_error_or_frozen_script_not_completed",
            "original_runner_result": runner,
        }
    return outcome


def compare_answer_view(results, packet, unblinding, sealed):
    from app.agents.versions import canonical_hash

    digest = canonical_hash(packet)
    if sealed["packet_hash"] != digest or unblinding["packet_hash"] != digest:
        raise ValueError("Human labels and answer packet do not match")
    identifiers = {item["id"] for item in packet["items"]}
    if (
        set(sealed["labels"]) != identifiers
        or set(unblinding["mapping"]) != identifiers
        or sealed.get("attestation") != "human_independent_answer_only"
        or sealed.get("assistant_prefilled") is not False
    ):
        raise ValueError("Independent annotation seal is incomplete")
    by_execution = {row["execution_id"]: row for row in results}
    items = []
    for identifier in sorted(identifiers):
        row = by_execution[unblinding["mapping"][identifier]["execution_id"]]
        label = sealed["labels"][identifier]
        if label not in {"problem", "no_visible_problem", "insufficient"}:
            raise ValueError("Unknown human label")
        gold = (
            None
            if row["classification"] == "unknown"
            else (
                "problem"
                if row["objective_count_violation"] or row.get("activated")
                else "no_visible_problem"
            )
        )
        items.append(
            {
                "id": identifier,
                "execution_id": row["execution_id"],
                "human_label": label,
                "sql_effect_gold": gold,
                "evidence_classification": row["classification"],
                "agreement": label == gold if gold and label != "insufficient" else None,
            }
        )
    comparable = [item for item in items if item["agreement"] is not None]
    return {
        "packet_hash": digest,
        "items": items,
        "total": len(items),
        "human_insufficient": sum(item["human_label"] == "insufficient" for item in items),
        "agreement_on_decisive_labels": {
            "numerator": sum(item["agreement"] for item in comparable),
            "denominator": len(comparable),
        },
        "boundary": "Human answer view versus SQL effects; no judge/model accuracy claim.",
    }
