"""One append boundary for ordered, typed, redacted execution observations."""

import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.executors.control import database_now, fence, locked_pair
from app.models import Artifact, TraceEvent
from app.schemas.events import EventPayload

PAYLOAD = TypeAdapter(EventPayload)
ROW_LIMIT = 24_576
ARTIFACT_LIMIT = 8 * 1024 * 1024
EVIDENCE_ROOT = PROJECT_ROOT / ".local/evidence"
SENSITIVE = {
    "apikey",
    "password",
    "passwordhash",
    "secret",
    "authorization",
    "cookie",
    "setcookie",
    "leasetoken",
    "accesstoken",
    "refreshtoken",
    "token",
}


def redact(value, settings):
    secrets = [
        getattr(settings, name).get_secret_value()
        for name in ("deepseek_api_key", "mysql_password", "redis_password", "demo_seed_password")
    ]

    def visit(item):
        if isinstance(item, dict):
            return {
                str(k): "[REDACTED]"
                if re.sub(r"[^a-z]", "", str(k).lower()) in SENSITIVE
                else visit(v)
                for k, v in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [visit(v) for v in item]
        if isinstance(item, str):
            for secret in secrets:
                if secret:
                    item = item.replace(secret, "[REDACTED]")
            item = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", item)
            return re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [REDACTED]", item)
        if isinstance(item, (UUID, datetime, Path)):
            return str(item)
        return item

    return visit(deepcopy(value))


def _artifact(session, run, identifier, payload):
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    if len(serialized) > ARTIFACT_LIMIT:
        raise AgentError("evidence_size_limit", "单份原始证据超过受控大小上限。", 503)
    directory = EVIDENCE_ROOT / run.run_uuid
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{identifier}.json"
    temporary = directory / f"{identifier}.{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(serialized)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    row = Artifact(
        run_id=run.id,
        kind="trace_payload",
        relative_path=path.relative_to(PROJECT_ROOT).as_posix(),
        sha256=hashlib.sha256(serialized).hexdigest(),
        size_bytes=len(serialized),
        generated_by_revision="T06-evidence-v1.1",
        metadata_json={"event_id": identifier},
    )
    session.add(row)
    session.flush()
    return {"artifact_id": row.id, "sha256": row.sha256, "bytes": row.size_bytes}


def append_event(
    session,
    run,
    execution,
    payload,
    *,
    settings=None,
    event_id=None,
    node=None,
    visit=None,
    tool_call_id=None,
    parent=None,
    attempt=None,
):
    """Caller owns the run lock. Trigger allocation commits with this transaction."""
    if run.manifest_json.get("trace_schema") != "1.1":
        return None
    settings = settings or Settings()
    payload = PAYLOAD.validate_python(redact(payload, settings)).model_dump(mode="json")
    identifier = str(event_id or uuid4())
    identity = {
        "payload": payload,
        "node": node,
        "visit": visit,
        "tool_call_id": tool_call_id,
        "parent": parent,
        "execution": execution.id if execution else None,
        "attempt": (attempt or execution.attempt_no) if execution else 1,
    }
    content_hash = canonical_hash(identity)
    existing = session.scalar(
        select(TraceEvent).where(TraceEvent.event_uuid == identifier).with_for_update()
    )
    if existing:
        if existing.run_id != run.id or existing.payload_json.get("content_hash") != content_hash:
            raise AgentError("event_id_conflict", "事件标识已绑定不同观察内容。", 409)
        return existing
    refs = []
    if len(json.dumps(payload, ensure_ascii=False).encode()) > ROW_LIMIT:
        ref = _artifact(session, run, identifier, payload)
        refs.append(f"artifact:{ref['artifact_id']}:{ref['sha256']}")
        # Complete sanitized original is in the immutable artifact; row remains bounded.
        for key in (
            "raw_record",
            "response",
            "arguments",
            "raw_result",
            "visible_result",
            "before",
            "after",
            "usage",
            "output",
            "result",
            "summary",
            "draft",
        ):
            if key in payload and payload[key] is not None:
                payload[key] = {"artifact": ref, "field": key}
        if "text" in payload:
            payload["text"] = None
        payload["artifact"] = ref
        payload = PAYLOAD.validate_python(payload).model_dump(mode="json")
    wrapped = {
        "schema_version": "1.1",
        "payload": payload,
        "evidence_refs": refs,
        "content_hash": content_hash,
    }
    if len(json.dumps(wrapped, ensure_ascii=False).encode()) > ROW_LIMIT:
        raise AgentError("event_payload_limit", "事件摘要超过事实行上限。", 503)
    attempt_no = (attempt or execution.attempt_no) if execution else 1
    seq = (
        (
            session.scalar(
                select(TraceEvent.seq_no)
                .where(TraceEvent.execution_id == execution.id, TraceEvent.attempt_no == attempt_no)
                .order_by(TraceEvent.seq_no.desc())
                .limit(1)
                .with_for_update()
            )
            or 0
        )
        + 1
        if execution
        else 1
    )
    row = TraceEvent(
        event_uuid=identifier,
        run_id=run.id,
        run_seq_no=0,
        execution_id=execution.id if execution else None,
        attempt_no=attempt_no,
        seq_no=seq,
        event_type=payload["kind"],
        node_name=node,
        node_visit_no=visit,
        tool_call_id=tool_call_id,
        parent_event_uuid=str(parent) if parent else None,
        occurred_at=database_now(session),
        payload_json=wrapped,
        payload_hash=canonical_hash(wrapped),
    )
    session.add(row)
    session.flush()
    session.refresh(row)  # MySQL BEFORE INSERT assigns the run order.
    return row


SEMANTIC_KEYS = (
    "turn_index",
    "fields",
    "confirmation_state",
    "confirmation_revision",
    "pending_draft",
    "grant",
    "denials",
    "business_result",
    "unknown_submission",
    "candidate_rooms",
    "evidence_refs",
    "compactions",
)


def semantic(state):
    return deepcopy({key: state.get(key) for key in SEMANTIC_KEYS})


class EvidenceRecorder:
    def __init__(self, control, settings, claim):
        self.control, self.settings, self.claim = control, settings, claim
        self.attempt = claim["attempt"]
        self.model_metadata = {
            "requested_model": claim["manifest"]["target"]["model_requested"],
            "prompt_hash": claim["manifest"]["target"]["prompt_hash"],
            "config_hash": claim["manifest"]["target"]["config_hash"],
        }

    def emit(
        self,
        kind,
        *,
        node=None,
        visit=None,
        tool_call_id=None,
        parent=None,
        event_id=None,
        **payload,
    ):
        with self.control.transaction() as (session, run, execution):
            row = append_event(
                session,
                run,
                execution,
                {"kind": kind, **payload},
                settings=self.settings,
                event_id=event_id,
                node=node,
                visit=visit,
                tool_call_id=tool_call_id,
                parent=parent,
                attempt=self.attempt,
            )
            return row.event_uuid if row else None

    def node_start(self, node):
        with self.control.transaction() as (session, run, execution):
            timing = deepcopy(execution.timing_json)
            all_visits = timing.setdefault("trace_visits", {})
            visits = all_visits.setdefault(str(self.attempt), {})
            visit = visits.get(node, 0) + 1
            visits[node] = visit
            execution.timing_json = timing
            row = append_event(
                session,
                run,
                execution,
                {"kind": "node.started", "status": "started"},
                settings=self.settings,
                node=node,
                visit=visit,
                attempt=self.attempt,
            )
            return row.event_uuid if row else None, visit

    def model_end(self, started, record, *, node="model", visit=None, error=None):
        """Late provider usage is evidence, not authority to execute another action."""
        with Session(self.control.engine) as session, session.begin():
            run, execution = locked_pair(session, self.control.execution_id)
            ticket = (execution.usage_json or {}).get("tickets", {}).get(record.get("call_id"))
            late = execution.lease_token != self.control.token or execution.status != "running"
            if ticket:
                if ticket["attempt"] != self.attempt:
                    raise AgentError("evidence_ticket_mismatch", "模型票据不属于该attempt。", 409)
            else:
                fence(session, execution.id, self.control.token)
            row = append_event(
                session,
                run,
                execution,
                {
                    "kind": "model.error" if error else "model.completed",
                    **self.model_metadata,
                    "call_id": record.get("call_id"),
                    "usage": record.get("usage"),
                    "response_model": record.get("response", {}).get("model_returned"),
                    "response": record.get("response"),
                    "raw_record": record,
                    "elapsed_ms": record.get("elapsed_ms"),
                    "code": error,
                    "model_requests": record.get("model_requests", 0),
                    "late_observation": late,
                },
                settings=self.settings,
                event_id=str(uuid5(UUID(started), "model.end")) if started else None,
                node=node,
                visit=visit,
                parent=started,
                attempt=self.attempt,
            )
            return row.event_uuid if row else None

    def business_snapshot(self):
        from app.executors.service import booking_snapshot

        with self.control.transaction() as (session, _, execution):
            return booking_snapshot(session, execution.scope_id)
