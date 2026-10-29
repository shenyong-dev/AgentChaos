"""Independent literal offline proposals. Not natural model quality or human decisions."""

from copy import deepcopy


def proposal(profile="normal", *, day="2026-10-29", wrong_count=False):
    attendees = 21 if profile == "capacity" else 18 if profile == "correction" else 10
    day = "2026-10-30" if profile == "correction" else day
    turns = [{"user": f"请预约{day}A101，14:00到15:00，{attendees}人；先出草稿，固定场地。"}]
    if profile != "capacity":
        turns.append({"user": "确认"})
    count = 0 if profile == "capacity" else 1
    assertions = [{"type": "booking_count", "equals": 1 if wrong_count else count}]
    if count:
        assertions.append(
            {"type": "booking_matches", "date": day, "attendees": attendees, "room_code": "A101"}
        )
    return deepcopy(
        {
            "profile": profile,
            "title": "显式离线夹具" + profile,
            "rationale": "独立编写的基础设施反例，非真实模型输出或人工审核。",
            "turns": turns,
            "assertions": assertions,
        }
    )
