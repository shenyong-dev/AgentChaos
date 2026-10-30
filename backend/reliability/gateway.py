"""Deterministic, explicitly offline booking flow for actual storage/Worker faults."""

import json
import os
import time
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.errors import AgentError


def tool(name, arguments):
    return AIMessage(content="", tool_calls=[{"id": str(uuid4()), "name": name, "args": arguments}])


class InfrastructureGateway:
    def __init__(self, control, *, signals=None, delay=0.15):
        self.control = control
        self.signals = Path(signals or os.environ["AC_T17_SIGNALS"])
        self.delay = delay

    def hold(self, session_id, phase):
        self.signals.mkdir(parents=True, exist_ok=True)
        prefix = self.signals / f"{session_id}-{phase}"
        prefix.with_suffix(".entered").write_text(str(os.getpid()), encoding="ascii")
        deadline = time.monotonic() + 90
        while not prefix.with_suffix(".release").exists():
            self.control.check()
            if time.monotonic() > deadline:
                raise AgentError("offline_control_timeout", "明确离线控制等待到期。", 504)
            time.sleep(0.1)

    def invoke(self, messages, tools, **kwargs):
        began = time.monotonic()
        facts = json.loads(str(messages[0].content).splitlines()[-1])
        inputs = [str(m.content) for m in messages if isinstance(m, HumanMessage)]
        latest = inputs[-1]
        whole = "\n".join(inputs)
        executed = [item["name"] for item in facts["executed_tools_this_turn"]]
        if "T17-HOLD-BEFORE" in latest and not executed:
            self.hold(facts["session_id"], "before")
        time.sleep(self.delay)
        self.control.check()
        response = self.choose(facts, latest, whole, executed)
        return response, {
            "call_id": "offline-" + str(uuid4()),
            "model_requests": 0,
            "response": {"model_returned": "T17-explicit-offline-infrastructure-control"},
            "chunks": [],
            "elapsed_ms": round((time.monotonic() - began) * 1000, 2),
        }

    def choose(self, facts, latest, whole, executed):
        if latest.strip() == "确认":
            if "confirm_booking" not in executed and facts.get("grant"):
                draft = facts["pending_draft"]
                return tool(
                    "confirm_booking",
                    {
                        "draft_id": draft["draft_id"],
                        "draft_hash": draft["draft_hash"],
                        "idempotency_key": draft["suggested_idempotency_key"],
                    },
                )
            if "T17-HOLD-AFTER-COMMIT" in whole:
                self.hold(facts["session_id"], "after")
            return AIMessage(content="明确离线模型控制：预约工具结果已返回，请按SQL证据判断。")
        if "T17-BOOK" not in latest:
            return AIMessage(content="明确离线基础设施控制：未请求预约；不是模型质量验收。")
        values = {
            "room_code": "A101",
            "start_at": "2026-10-26T14:00:00+08:00",
            "end_at": "2026-10-26T15:00:00+08:00",
            "attendees": 10,
        }
        if "update_booking_request" not in executed:
            return tool(
                "update_booking_request",
                {
                    "date": "2026-10-26",
                    "start_time": "14:00",
                    "end_time": "15:00",
                    "attendees": 10,
                    "room_code": "A101",
                    "source_quotes": {
                        "date": "明天",
                        "start_time": "14:00",
                        "end_time": "15:00",
                        "attendees": "10人",
                        "room_code": "A101",
                    },
                },
            )
        if "retrieve_rules" not in executed:
            return tool("retrieve_rules", {"query": "预约人数时间确认规则"})
        if "query_availability" not in executed:
            return tool("query_availability", values)
        if "preview_booking" not in executed:
            return tool("preview_booking", values)
        return AIMessage(content="明确离线控制：完整草稿等待确认。")
