"""One owned SQL lock fences a refund round; interrupted rounds are never replayed."""

import json
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from app.agents.errors import AgentError
from app.core.deployment import source_fingerprint


class RefundControl:
    def __init__(self, connection, lock_name, redis, key, token, source):
        self.connection, self.lock_name, self.redis, self.key = connection, lock_name, redis, key
        self.token, self.source = token, source
        self.deadline, self.steps = time.monotonic() + 120, 0

    def check(self):
        if time.monotonic() >= self.deadline:
            raise AgentError("agent_deadline", "本轮已达到时限，请核对原回执与账本。", 504)
        held = self.connection.scalar(
            text("SELECT IS_USED_LOCK(:name)=CONNECTION_ID()"), {"name": self.lock_name}
        )
        meta = json.loads(self.redis.get(self.key) or "{}")
        if (
            held != 1
            or (meta.get("inflight") or {}).get("token") != self.token
            or source_fingerprint() != self.source
        ):
            raise AgentError("refund_round_fenced", "原操作或源码已变化，禁止继续写入。", 409)

    @contextmanager
    def transaction(self):
        self.check()
        yield
        self.check()

    def step(self, kind):
        self.steps += 1
        if self.steps > 40:
            raise AgentError("refund_step_limit", "本轮工具步骤达到上限。", 429)
        self.check()


class RefundObserver:
    def __init__(self, redis, key, control, manifest, request_id):
        self.redis, self.key, self.control, self.request_id = redis, key, control, request_id
        self.visits = {}
        self.model_metadata = {
            "requested_model": manifest["model_requested"],
            "config_hash": manifest["config_hash"],
            "prompt_hash": manifest["prompt_hash"],
        }

    def emit(self, kind, **payload):
        self.control.check()
        identifier = str(uuid4())
        self.redis.rpush(
            self.key,
            json.dumps(
                {
                    "event_id": identifier,
                    "type": kind,
                    "request_id": self.request_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "payload": payload,
                },
                ensure_ascii=False,
            ),
        )
        return identifier

    def node_start(self, node):
        visit = self.visits.get(node, 0) + 1
        self.visits[node] = visit
        return self.emit("node.started", node=node, visit=visit), visit

    def model_end(self, started, record, *, visit=None, error=None):
        return self.emit(
            "model.error" if error else "model.completed",
            parent=started,
            visit=visit,
            call_id=record.get("call_id"),
            usage=record.get("usage"),
            response_model=record.get("response", {}).get("model_returned"),
            model_requests=record.get("model_requests", 0),
            code=error,
        )
