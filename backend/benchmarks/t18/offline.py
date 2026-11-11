"""Explicit tool-wiring control, not a language understanding score."""

import json
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


class OfflineGateway:
    def invoke(self, messages, tools, **kwargs):
        runtime = json.loads(
            [m.content for m in messages if isinstance(m, SystemMessage)][-1].split("：", 1)[1]
        )
        humans = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
        text = messages[humans[-1]].content
        observations = [
            json.loads(m.content) for m in messages[humans[-1] + 1 :] if isinstance(m, ToolMessage)
        ]

        def call(name, args):
            return AIMessage(
                content="", tool_calls=[{"id": str(uuid4()), "name": name, "args": args}]
            )

        order = "O-1002" if "O-1002" in text else "O-2001" if "O-2001" in text else "O-1001"
        draft = runtime["pending_draft"]
        if any(x in text for x in ("拒绝", "不要退款")):
            msg = AIMessage(content="明确离线控制：已拒绝，不模拟入账。")
        elif observations:
            last = observations[-1]
            if last.get("code") == "response_lost":
                msg = call("lookup_refund_by_request", {"idempotency_key": last["request_key"]})
            elif last.get("code") == "order_checked" and last["data"]["refundable"]:
                msg = call("preview_refund", {"order_id": order})
            else:
                msg = AIMessage(content="明确离线控制：请核对模拟账本。")
        elif text.startswith("确认"):
            msg = (
                call(
                    "confirm_refund",
                    {
                        "draft_id": draft["draft_id"],
                        "draft_hash": draft["draft_hash"],
                        "idempotency_key": draft["request_key"],
                    },
                )
                if draft and runtime["grant"]
                else AIMessage(content="明确离线控制：没有当前可确认草稿，不提交。")
            )
        else:
            msg = call("query_order", {"order_id": order})
        return msg, {
            "call_id": "offline-" + str(uuid4()),
            "model_requests": 0,
            "response": {"model_returned": "offline-test-double"},
        }
