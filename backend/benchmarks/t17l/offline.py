"""Deterministic wiring control, explicitly not DeepSeek quality evidence."""

import json
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


class OfflineGateway:
    def __init__(self, group):
        self.group = group

    def invoke(self, messages, tools, **kwargs):
        humans = [i for i, message in enumerate(messages) if isinstance(message, HumanMessage)]
        current = str(messages[humans[-1]].content)
        observations = [m for m in messages[humans[-1] + 1 :] if isinstance(m, ToolMessage)]
        history = [m for m in messages if isinstance(m, ToolMessage)]

        def call(name, args):
            return AIMessage(
                content="", tool_calls=[{"id": str(uuid4()), "name": name, "args": args}]
            )

        def reply(text):
            return AIMessage(content="离线接线控制（非真实模型）：" + text)

        values = {
            "start_at": "2026-11-07T06:00:00+00:00",
            "end_at": "2026-11-07T07:00:00+00:00",
            "attendees": 2 if self.group == "signed" else 8,
            "room_code": "A101",
        }
        if "拒绝确认" in current:
            message = reply("不再提交预约。")
        elif current.startswith("确认"):
            previews = [
                json.loads(m.content)
                for m in history
                if json.loads(m.content).get("code") == "confirmation_required"
            ]
            drafts = [v["data"] for v in previews if v.get("status") == "ok"]
            if not drafts:
                message = reply("没有可确认草稿，请提供有效人数。")
            elif not observations:
                draft = drafts[-1]
                message = call(
                    "confirm_booking",
                    {
                        "draft_id": draft["draft_id"],
                        "draft_hash": draft["draft_hash"],
                        "idempotency_key": draft["suggested_idempotency_key"],
                    },
                )
            elif json.loads(observations[-1].content).get("code") == "response_lost":
                message = call(
                    "lookup_booking_by_request",
                    {"idempotency_key": drafts[-1]["suggested_idempotency_key"]},
                )
            else:
                message = reply("工具返回已收到，请核对数据库证据。")
        elif not observations:
            message = call(
                "update_booking_request",
                {
                    "date": "2026-11-07",
                    "start_time": "14:00",
                    "end_time": "15:00",
                    "attendees": values["attendees"],
                    "room_code": "A101",
                    "source_quotes": {
                        "date": "2026-11-07",
                        "start_time": "14:00",
                        "end_time": "15:00",
                        "attendees": f"{values['attendees']}人",
                        "room_code": "A101",
                    },
                },
            )
        elif json.loads(observations[-1].content).get("status") == "error":
            message = reply("人数或请求不满足规则，需补充有效条件。")
        elif len(observations) == 1:
            message = call("retrieve_rules", {"query": "预约 容量 时间 确认"})
        elif len(observations) == 2:
            message = call("query_availability", values)
        elif len(observations) == 3:
            message = call("preview_booking", values)
        else:
            message = reply("草稿已生成，等待确认。")
        return message, {
            "call_id": "offline-" + str(uuid4()),
            "model_requests": 0,
            "chunks": [],
            "response": {"model_returned": "offline-test-double"},
        }
