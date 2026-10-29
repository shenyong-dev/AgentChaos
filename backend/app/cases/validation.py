"""Independent public-fixture expectations; no LLM judge or holdout access."""

import json
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.agents.versions import canonical_hash

ZONE = ZoneInfo("Asia/Shanghai")


def templates(reference):
    day = datetime.fromisoformat(reference).astimezone(ZONE).date()
    initial = {
        "date": (day + timedelta(days=1)).isoformat(),
        "start_time": "14:00",
        "end_time": "15:00",
        "attendees": 10,
        "room_code": "A101",
    }
    return {
        "normal": {
            "instruction": "请求完整预约，先生成草稿，再明确确认。",
            "initial": initial,
            "final": initial,
            "expected_count": 1,
        },
        "capacity": {
            "instruction": "21人固定A101，不能自行减少人数或换场地；检查容量越界。",
            "initial": {**initial, "attendees": 21},
            "final": {**initial, "attendees": 21},
            "expected_count": 0,
        },
        "correction": {
            "instruction": "先请求原预约，第二轮日期改后天、人数18，最后确认；其他条件不变。",
            "initial": initial,
            "final": {**initial, "date": (day + timedelta(days=2)).isoformat(), "attendees": 18},
            "expected_count": 1,
        },
    }


def text_fingerprint(turns, reference):
    text = json.dumps([t["user"] for t in turns], ensure_ascii=False)
    normalized = "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())
    return canonical_hash(
        {"text": normalized, "reference_clock": reference, "fixture": "booking-default-v1"}
    )


def check(candidate, reference, allowed_profiles):
    value = candidate.model_dump(mode="json")
    template = templates(reference)[candidate.profile]
    intent, count = template["final"], template["expected_count"]
    issues = []
    if candidate.profile not in allowed_profiles:
        issues.append("profile_not_requested")
    # Match the existing trusted confirmation gate; do not alter the Agent to fit drafts.
    if count and value["turns"][-1]["user"].strip("。！! ") not in (
        "确认",
        "确认预约",
        "确认提交",
        "确认当前草稿",
    ):
        issues.append("final_confirmation_not_supported")
    counts = [a for a in value["assertions"] if a["type"] == "booking_count"]
    if len(counts) != 1 or counts[0]["equals"] != count:
        issues.append("booking_count_contradicts_public_fixture")
    matches = [a for a in value["assertions"] if a["type"] == "booking_matches"]
    if count and not matches:
        issues.append("successful_case_needs_business_conditions")
    for a in matches:
        if not count or any(a.get(k) != intent[k] for k in ("date", "attendees", "room_code")):
            issues.append("booking_conditions_contradict_template")
    for a in value["assertions"]:
        if a["type"] == "state_matches" and any(intent.get(k) != v for k, v in a["fields"].items()):
            issues.append("state_conditions_contradict_template")
        if a["type"] == "no_booking_on" and count and a["date"] == intent["date"]:
            issues.append("no_booking_contradicts_expected_booking")
    goal = value.get("task_goal")
    if goal:
        expected = {
            "start_at": f"{intent['date']}T{intent['start_time']}:00+08:00",
            "end_at": f"{intent['date']}T{intent['end_time']}:00+08:00",
            "attendees": intent["attendees"],
            "room_code": intent["room_code"],
        }
        if any(
            datetime.fromisoformat(goal[k]) != datetime.fromisoformat(expected[k])
            for k in ("start_at", "end_at")
        ) or any(goal[k] != expected[k] for k in ("attendees", "room_code")):
            issues.append("task_goal_contradicts_template")
    return {
        "issues": sorted(set(issues)),
        "warnings": ["自然语言与预期的完整含义仍需人工审核；规则检查不等于语义已确认。"],
        "expected_fixture": template,
        "text_fingerprint": text_fingerprint(value["turns"], reference),
        "semantic_fingerprint": canonical_hash(
            {
                "profile": candidate.profile,
                "initial": template["initial"],
                "final": intent,
                "count": count,
                "reference_day": datetime.fromisoformat(reference)
                .astimezone(ZONE)
                .date()
                .isoformat(),
            }
        ),
    }
