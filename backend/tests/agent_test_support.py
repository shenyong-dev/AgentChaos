"""Explicit offline model double for infrastructure tests; no provider requests."""

from collections import deque
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from langchain_core.messages import AIMessage

REFERENCE = datetime(2026, 10, 25, 1, tzinfo=UTC)


def tool(name, args):
    return AIMessage(content="", tool_calls=[{"id": str(uuid4()), "name": name, "args": args}])


class OfflineGateway:
    def __init__(self):
        self.queue, self.calls = deque(), []

    def invoke(self, messages, tools, **kwargs):
        self.calls.append(messages)
        queued = self.queue.popleft()
        if isinstance(queued, Exception):
            raise queued
        message = queued(messages) if callable(queued) else queued
        return message, {
            "call_id": "offline-" + str(uuid4()),
            "model_requests": 0,
            "chunks": [],
            "response": {"model_returned": "offline-test-double"},
        }

    def preview(self, *, room="A101", attendees=10, text=None):
        values = {
            "start_at": (REFERENCE + timedelta(days=1, hours=5)).isoformat(),
            "end_at": (REFERENCE + timedelta(days=1, hours=6)).isoformat(),
            "attendees": attendees,
            "room_code": room,
        }
        self.queue.extend(
            [
                tool(
                    "update_booking_request",
                    {
                        "date": "2026-10-26",
                        "start_time": "14:00",
                        "end_time": "15:00",
                        "attendees": attendees,
                        "room_code": room,
                        "source_quotes": {
                            "date": "明天",
                            "start_time": "14:00",
                            "end_time": "15:00",
                            "attendees": f"{attendees}人",
                            "room_code": room,
                        },
                    },
                ),
                tool("retrieve_rules", {"query": "人数容量时间确认规则"}),
                tool("query_availability", values),
                tool("preview_booking", values),
                AIMessage(content="离线夹具：完整草稿等待确认，不代表真实模型验收。"),
            ]
        )
        return text or f"明天{room}，14:00到15:00，{attendees}人。"

    def confirm(self, view):
        draft = view["pending_draft"]
        self.queue.extend(
            [
                tool(
                    "confirm_booking",
                    {
                        "draft_id": draft["draft_id"],
                        "draft_hash": draft["draft_hash"],
                        "idempotency_key": draft["suggested_idempotency_key"],
                    },
                ),
                AIMessage(content="离线夹具：数据库结果已返回。"),
            ]
        )


def message(view, text):
    return {"request_id": str(uuid4()), "expected_revision": view["revision"], "text": text}


def resume(view, decision="confirm"):
    draft = view["pending_draft"]
    return {
        "request_id": str(uuid4()),
        "expected_revision": view["revision"],
        "decision": decision,
        "draft_id": draft["draft_id"],
        "draft_hash": draft["draft_hash"],
        "confirmation_revision": view["confirmation_revision"],
    }
