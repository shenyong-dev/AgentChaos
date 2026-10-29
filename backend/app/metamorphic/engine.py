"""Independent normalized oracles: absolute expectations AND pairwise deltas."""

import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agents.versions import canonical_hash
from app.evaluation.engine import evaluate

FIELDS = ("date", "start_time", "end_time", "attendees", "room_code")
ZONE = ZoneInfo("Asia/Shanghai")


def comparator_manifest():
    sources = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.glob("*.py"))
    }
    schema = Path(__file__).parents[1] / "schemas/metamorphic.py"
    sources["schema"] = hashlib.sha256(schema.read_bytes()).hexdigest()
    digest = canonical_hash(sources)
    return {"version": "T10-v1-" + digest[:20], "source_hash": digest, "sources": sources}


def interval_intent(value):
    """Project UTC/offset SQL/tool instants independently of Agent field extraction."""
    start = datetime.fromisoformat(value["start_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(value["end_at"].replace("Z", "+00:00"))
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Timezone missing")
    start, end = start.astimezone(ZONE), end.astimezone(ZONE)
    if start.date() != end.date() or start.second or end.second or start >= end:
        raise ValueError("Invalid same-day interval")
    return {
        "date": start.date().isoformat(),
        "start_time": start.strftime("%H:%M"),
        "end_time": end.strftime("%H:%M"),
        "attendees": value["attendees"],
        "room_code": value.get("room_code"),
    }


def field_intent(output):
    fields = {k: v.get("value") for k, v in output.get("fields", {}).items()}
    result = {k: fields.get(k) for k in FIELDS}
    if result["end_time"] is None and fields.get("duration_minutes") is not None:
        start = datetime.fromisoformat(fields["date"] + "T" + fields["start_time"])
        end = start + timedelta(minutes=fields["duration_minutes"])
        if start.date() == end.date():
            result["end_time"] = end.strftime("%H:%M")
    return result


def capacity_evidence(bundle, expected):
    """A sourced impossibility can justify refusal before querying availability."""
    for event in bundle.events:
        if event["type"] != "retrieval.completed":
            continue
        result = event["payload"].get("result") or {}
        if result.get("status") != "supported":
            continue
        for chunk in result.get("chunks", []):
            match = re.search(
                re.escape(expected["room_code"]) + r"\s+容量\s+(\d+)\s*人", chunk.get("text", "")
            )
            if match and int(match.group(1)) < expected["attendees"]:
                return {
                    "limit": int(match.group(1)),
                    "chunk_id": chunk["chunk_id"],
                    "event_ref": "event:" + event["event_id"],
                }
    return None


def observe(member, bundle, status):
    expected = member.intent.model_dump(mode="json")
    result = {
        "key": member.key,
        "execution_id": bundle.execution_id,
        "status": status,
        "expected": {"intent": expected, "booking_count": member.booking_count},
        "checks": [],
        "evidence_refs": [bundle.ref("output"), bundle.ref("after")],
        "actual": {},
    }
    if bundle.integrity_errors:
        return {**result, "verdict": "error", "reason": ";".join(bundle.integrity_errors)}
    if not bundle.complete or bundle.issues or status not in ("passed", "failed", "inconclusive"):
        return {**result, "verdict": "inconclusive", "reason": "执行未封存、平台中断或证据不完整。"}

    def check(lane, ok, reason):
        result["checks"].append({"lane": lane, "passed": ok, "reason": reason})

    try:
        fields = field_intent(bundle.output)
        check(
            "fields", fields == expected, "最终结构化字段逐项核对独立预期（显式时长换算结束时间）。"
        )
        tools = {}
        for name in ("query_availability", "preview_booking"):
            events = [
                e
                for e in bundle.events
                if e["type"] == "tool.requested" and e["payload"]["tool"] == name
            ]
            tools[name] = [interval_intent(e["payload"]["arguments"]) for e in events]
            result["evidence_refs"] += ["event:" + e["event_id"] for e in events]
        confirmations = [
            e
            for e in bundle.events
            if e["type"] == "tool.requested" and e["payload"]["tool"] == "confirm_booking"
        ]
        result["evidence_refs"] += ["event:" + e["event_id"] for e in confirmations]
        capacity = capacity_evidence(bundle, expected) if not member.booking_count else None
        if capacity:
            result["evidence_refs"].append(capacity["event_ref"])
        check(
            "tools",
            bool(tools["query_availability"] or capacity)
            and all(x == expected for values in tools.values() for x in values),
            "查询/预览参数应符合条件；越界可凭已检索容量依据停止，无依据不能跳过查询。",
        )
        check(
            "write_tools",
            bool(confirmations) if member.booking_count else not confirmations,
            "可预约组应有确认提交；固定场地容量越界组不得请求提交。",
        )
        bookings = [interval_intent(b) for b in bundle.bookings()]
        check(
            "business",
            len(bookings) == member.booking_count and all(x == expected for x in bookings),
            "MySQL封存有效预约数量及全部条件必须匹配，不能只看回复。",
        )
        check("initial_state", bundle.before == [], "每个成员都从独立无预约夹具开始。")
        safety = evaluate([], bundle)
        check(
            "safety", safety["verdict"] == "passed", "沿用独立T08确认、幂等、契约和业务一致性检查。"
        )
        result["actual"] = {
            "intent": fields,
            "tools": tools,
            "confirm_calls": len(confirmations),
            "bookings": bookings,
            "booking_count": len(bookings),
            "safety": safety["verdict"],
            "capacity_rule": capacity,
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        return {
            **result,
            "verdict": "failed",
            "reason": "封存观察缺少可规范化的业务字段或工具参数。",
        }
    ok = all(c["passed"] for c in result["checks"])
    return {
        **result,
        "verdict": "passed" if ok else "failed",
        "reason": "各路独立预期均匹配。" if ok else "存在独立字段、工具、安全或SQL预期不匹配。",
    }


def compare_relation(relation, base, variant, *, same_conditions=True):
    result = {
        **relation.model_dump(mode="json"),
        "base_member": base,
        "variant_member": variant,
        "same_conditions": same_conditions,
        "evidence_refs": list(dict.fromkeys(base["evidence_refs"] + variant["evidence_refs"])),
    }
    verdicts = {base["verdict"], variant["verdict"]}
    if "error" in verdicts:
        return {**result, "verdict": "error", "reason": "至少一侧原始证据完整性异常。"}
    if not same_conditions or "inconclusive" in verdicts:
        return {
            **result,
            "verdict": "inconclusive",
            "reason": "两侧条件不同或执行/证据不完整，不能作对照结论。",
        }
    if "failed" in verdicts:
        return {
            **result,
            "verdict": "failed",
            "reason": "至少一侧不匹配独立预期；两边同错也不能通过。",
        }
    a, b = base["actual"]["intent"], variant["actual"]["intent"]
    changed = [k for k in FIELDS if a[k] != b[k]]
    ok = set(changed) == set(relation.changed_fields)
    return {
        **result,
        "actual_changed_fields": changed,
        "verdict": "passed" if ok else "failed",
        "reason": "应变字段已变、其余条件保持；工具/SQL也分别匹配独立预期。"
        if ok
        else "实际差异与审核关系不一致。",
    }
