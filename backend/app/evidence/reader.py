"""Read-only snapshots and committed event cursors. Never reads graph state to replay it."""

import hashlib
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.evidence.recorder import EVIDENCE_ROOT
from app.executors.service import RunService
from app.models import Artifact, TestExecution, TestRun, TraceEvent
from app.schemas.events import TraceEvent as WireEvent


def cursor(run_uuid, seq=0, identifier=None):
    return f"v1:{run_uuid}:{seq}:{identifier or '0'}"


def checked_cursor(session, run, value):
    if not value:
        return 0
    try:
        version, owner, seq, identifier = value.split(":")
        if version != "v1" or str(UUID(owner)) != owner or not seq.isdecimal():
            raise ValueError()
        number = int(seq)
        if number and str(UUID(identifier)) != identifier:
            raise ValueError()
        if not number and identifier != "0":
            raise ValueError()
    except (ValueError, TypeError):
        raise AgentError("cursor_invalid", "事件游标格式不正确。", 400) from None
    if owner != run.run_uuid:
        raise AgentError("cursor_run_mismatch", "游标不属于该运行，请重新读取快照。", 409)
    if number:
        row = session.scalar(
            select(TraceEvent.event_uuid).where(
                TraceEvent.run_id == run.id, TraceEvent.run_seq_no == number
            )
        )
        if row != identifier:
            raise AgentError(
                "cursor_unavailable", "游标不可续传，snapshot_required：请重取快照。", 410
            )
    return number


def item(row, run, execution_uuid):
    wrapped = row.payload_json
    is_current = wrapped.get("schema_version") == "1.1"
    if is_current and canonical_hash(wrapped) != row.payload_hash:
        raise AgentError("evidence_integrity_error", "事件内容与持久哈希不符。", 503)
    event = {
        "schema_version": "1.1" if is_current else "1.0",
        "event_id": row.event_uuid,
        "run_id": run.run_uuid,
        "execution_id": execution_uuid,
        "scope": "execution" if execution_uuid else "run",
        "attempt": row.attempt_no if execution_uuid else None,
        "seq": row.seq_no,
        "run_seq": row.run_seq_no,
        "type": row.event_type,
        "node": row.node_name,
        "node_visit": row.node_visit_no,
        "tool_call_id": row.tool_call_id,
        "parent_event_id": row.parent_event_uuid,
        "timestamp": row.occurred_at.isoformat(),
        "payload": wrapped["payload"] if is_current else wrapped,
        "evidence_refs": wrapped.get("evidence_refs", []) if is_current else [],
    }
    if is_current:
        event = WireEvent.model_validate(event).model_dump(mode="json")
    return {"cursor": cursor(run.run_uuid, row.run_seq_no, row.event_uuid), "event": event}


def pending_models(session, run_id):
    end = aliased(TraceEvent)
    return session.scalar(
        select(func.count())
        .select_from(TraceEvent)
        .where(
            TraceEvent.run_id == run_id,
            TraceEvent.event_type == "model.started",
            ~select(end.id)
            .where(
                end.parent_event_uuid == TraceEvent.event_uuid,
                end.event_type.in_(("model.completed", "model.error")),
            )
            .exists(),
        )
    )


class TraceReader:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    def snapshot(self, user_id, run_uuid):
        with Session(self.engine) as session:
            run = RunService.owned_run(session, user_id, run_uuid)
            rows = list(
                session.scalars(
                    select(TestExecution)
                    .where(TestExecution.run_id == run.id)
                    .order_by(TestExecution.id)
                )
            )
            tail = session.scalar(
                select(TraceEvent)
                .where(
                    TraceEvent.run_id == run.id,
                    TraceEvent.run_seq_no <= max(0, run.event_seq - 100),
                )
                .order_by(TraceEvent.run_seq_no.desc())
                .limit(1)
            )
            last = session.scalar(
                select(TraceEvent)
                .where(TraceEvent.run_id == run.id)
                .order_by(TraceEvent.run_seq_no.desc())
                .limit(1)
            )
            return {
                "run_id": run.run_uuid,
                "status": run.status,
                "cursor": cursor(run.run_uuid, last.run_seq_no, last.event_uuid)
                if last
                else cursor(run.run_uuid),
                "tail_cursor": cursor(run.run_uuid, tail.run_seq_no, tail.event_uuid)
                if tail
                else cursor(run.run_uuid),
                "event_count": run.event_seq,
                "manifest_hash": run.manifest_hash,
                "manifest": run.manifest_json,
                "budget": run.budget_json,
                "summary": run.summary_json,
                "pending_model_observations": pending_models(session, run.id),
                "executions": [RunService.execution_view(e) for e in rows],
            }

    def page(self, user_id, run_uuid, *, after=None, limit=100, execution_uuid=None):
        with Session(self.engine) as session:
            run = RunService.owned_run(session, user_id, run_uuid)
            sequence = checked_cursor(session, run, after)
            query = (
                select(TraceEvent, TestExecution.execution_uuid)
                .outerjoin(TestExecution, TraceEvent.execution_id == TestExecution.id)
                .where(TraceEvent.run_id == run.id, TraceEvent.run_seq_no > sequence)
            )
            if execution_uuid:
                execution = session.scalar(
                    select(TestExecution.id).where(
                        TestExecution.run_id == run.id,
                        TestExecution.execution_uuid == str(execution_uuid),
                    )
                )
                if execution is None:
                    raise AgentError("execution_not_found", "找不到执行。", 404)
                query = query.where(TraceEvent.execution_id == execution)
            found = session.execute(query.order_by(TraceEvent.run_seq_no).limit(limit + 1)).all()
            items = [item(row, run, identifier) for row, identifier in found[:limit]]
            return {
                "items": items,
                "next_cursor": items[-1]["cursor"] if items else (after or cursor(run.run_uuid)),
                "has_more": len(found) > limit,
                "high_watermark": run.event_seq,
                "run_status": run.status,
                "pending_model_observations": pending_models(session, run.id),
            }

    def execution(self, user_id, execution_uuid):
        with Session(self.engine) as session:
            found = session.execute(
                select(TestExecution, TestRun.run_uuid)
                .join(TestRun)
                .where(
                    TestExecution.execution_uuid == str(execution_uuid),
                    TestRun.created_by == user_id,
                )
            ).one_or_none()
            if found is None:
                raise AgentError("execution_not_found", "找不到执行。", 404)
            row, run_uuid = found
            return {**RunService.execution_view(row), "run_id": run_uuid}

    def artifact(self, user_id, run_uuid, artifact_id):
        with Session(self.engine) as session:
            run = RunService.owned_run(session, user_id, run_uuid)
            row = session.scalar(
                select(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.run_id == run.id,
                    Artifact.kind == "trace_payload",
                )
            )
            if row is None:
                raise AgentError("artifact_not_found", "找不到证据。", 404)
            path = (PROJECT_ROOT / row.relative_path).resolve()
            allowed = (EVIDENCE_ROOT / run.run_uuid).resolve()
            if not path.is_relative_to(allowed) or not path.is_file():
                raise AgentError("artifact_unavailable", "证据文件不可读取。", 503)
            payload = path.read_bytes()
            if len(payload) != row.size_bytes or hashlib.sha256(payload).hexdigest() != row.sha256:
                raise AgentError("artifact_integrity_error", "证据文件与哈希不符。", 503)
            return path, row.sha256
