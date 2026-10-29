"""Explicit infrastructure double. Uses annotated values; never tests LLM understanding."""

import json

from langchain_core.messages import AIMessage


def make_gateway(member):
    from agent_test_support import OfflineGateway, tool

    gateway = OfflineGateway()
    fields = member.intent.model_dump(mode="json")
    if member.key in ("DURATION", "LONGER"):
        fields.pop("end_time")
        fields["duration_minutes"] = 60 if member.key == "DURATION" else 90
    patch = {**fields, "source_quotes": {k: member.turns[0] for k in fields}}
    expected = member.intent.model_dump(mode="json")
    args = {
        "start_at": f"{expected['date']}T{expected['start_time']}:00+08:00",
        "end_at": f"{expected['date']}T{expected['end_time']}:00+08:00",
        "room_code": expected["room_code"],
        "attendees": expected["attendees"],
    }
    gateway.queue.extend(
        [
            tool("update_booking_request", patch),
            tool("retrieve_rules", {"query": "人数容量时间确认规则"}),
            tool("query_availability", args),
        ]
    )
    if member.booking_count:

        def confirm(messages):
            # Read the actual server draft; never invent a confirmation/hash.
            for msg in reversed(messages):
                if getattr(msg, "type", "") == "tool":
                    result = json.loads(msg.content)
                    if result.get("code") != "confirmation_required":
                        continue
                    draft = result["data"]
                    return tool(
                        "confirm_booking",
                        {
                            "draft_id": draft["draft_id"],
                            "draft_hash": draft["draft_hash"],
                            "idempotency_key": draft["suggested_idempotency_key"],
                        },
                    )
            raise ValueError("Actual draft missing in offline harness")

        gateway.queue.extend(
            [
                tool("preview_booking", args),
                AIMessage(content="离线工具夹具：真实草稿已生成，等待用户确认。"),
                confirm,
                AIMessage(content="离线工具夹具：预约结果已返回，不代表真实模型能力。"),
            ]
        )
    else:
        gateway.queue.extend(
            [
                AIMessage(content="离线工具夹具：A101容量不足，未提交。"),
                AIMessage(content="离线工具夹具：仍不提交，也不更换场地。"),
            ]
        )
    return gateway
