"""Fixed development control, not a language model or semantic quality evidence."""

import json
import re
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage


def tool(name, args):
    return AIMessage(content="", tool_calls=[{"id": str(uuid4()), "name": name, "args": args}])


class ControlledGateway:
    def __init__(self):
        self.calls = []

    def invoke(self, messages, tools, **kwargs):
        facts = json.loads(messages[0].content.rsplit("\n", 1)[-1])
        self.calls.append(messages)
        used = {x["name"] for x in facts["executed_tools_this_turn"]}
        text = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
        fields = {k: v.get("value") for k, v in facts["fields"].items()}
        draft = facts.get("pending_draft")
        if facts.get("unknown_submission") and "lookup_booking_by_request" not in used:
            response = tool(
                "lookup_booking_by_request",
                {"idempotency_key": facts["unknown_submission"]["request_key"]},
            )
        elif (
            facts["confirmation_state"] == "authorized" and draft and "confirm_booking" not in used
        ):
            response = tool(
                "confirm_booking",
                {k: draft[k] for k in ("draft_id", "draft_hash", "suggested_idempotency_key")},
            )
            response.tool_calls[0]["args"]["idempotency_key"] = response.tool_calls[0]["args"].pop(
                "suggested_idempotency_key"
            )
        elif facts["confirmation_state"] in ("rejected", "consumed") or "confirm_booking" in used:
            response = AIMessage(content="明确离线控制：本轮已结束，请以右侧真实 SQL 查证为准。")
        elif "update_booking_request" not in used:
            patch, quotes = {}, {}
            if "明天" in text:
                patch["date"] = (
                    datetime.fromisoformat(facts["reference_clock"])
                    .astimezone(ZoneInfo("Asia/Shanghai"))
                    .date()
                    + timedelta(days=1)
                ).isoformat()
                quotes["date"] = "明天"
            for key, word in (
                ("start_time", "14:00"),
                ("end_time", "15:00"),
                ("room_code", "A101"),
            ):
                if word in text:
                    patch[key] = word
                    quotes[key] = word
            match = re.search(r"(8|12)人", text)
            if match:
                patch["attendees"] = int(match[1])
                quotes["attendees"] = match[0]
            if patch:
                response = tool("update_booking_request", {**patch, "source_quotes": quotes})
            else:
                response = AIMessage(
                    content="明确离线控制仅支持明天A101、14:00到15:00、8人或12人的固定开发输入。"
                )
        elif not all(
            fields.get(k) for k in ("date", "start_time", "end_time", "attendees", "room_code")
        ):
            response = AIMessage(content="明确离线控制：请补充缺失条件，例如人数。")
        elif "retrieve_rules" not in used:
            response = tool("retrieve_rules", {"query": "容量时间确认规则"})
        elif "query_availability" not in used or "preview_booking" not in used:
            args = {
                "room_code": fields["room_code"],
                "attendees": fields["attendees"],
                "start_at": fields["date"] + "T" + fields["start_time"] + ":00+08:00",
                "end_at": fields["date"] + "T" + fields["end_time"] + ":00+08:00",
            }
            response = tool(
                "query_availability" if "query_availability" not in used else "preview_booking",
                args,
            )
        else:
            response = AIMessage(
                content="明确离线控制：完整草稿已生成，等待你核对并确认。尚未预约。"
            )
        return response, {
            "call_id": "offline-" + str(uuid4()),
            "model_requests": 0,
            "chunks": [],
            "response": {"model_returned": "offline_control"},
        }
