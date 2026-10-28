"""SQL evidence loader and append-only evaluation snapshots; no business side effects."""

import hashlib
import json
from copy import deepcopy
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.evaluation.engine import Evidence, evaluate, evaluator_manifest
from app.evidence.recorder import EVIDENCE_ROOT, append_event, redact
from app.executors.control import TERMINAL
from app.models import Artifact, AssertionResult, TestExecution, TestRun, TraceEvent


def load_evidence(session, run, execution, *, closing=False):
    events, issues, errors, cache = [], [], [], {}
    rows = list(
        session.scalars(
            select(TraceEvent)
            .where(TraceEvent.execution_id == execution.id)
            .order_by(TraceEvent.run_seq_no)
            .with_for_update()
        )
    )
    for attempt in {r.attempt_no for r in rows}:
        numbers = [r.seq_no for r in rows if r.attempt_no == attempt]
        if numbers != list(range(1, max(numbers) + 1)):
            issues.append(f"execution_event_sequence_gap:{attempt}")

    def artifact(ref):
        identifier = ref["artifact_id"]
        if identifier not in cache:
            row = session.get(Artifact, identifier)
            if row is None or row.run_id != run.id or row.kind != "trace_payload":
                raise ValueError("artifact ownership mismatch")
            path = (PROJECT_ROOT / row.relative_path).resolve()
            if not path.is_relative_to((EVIDENCE_ROOT / run.run_uuid).resolve()):
                raise ValueError("artifact path mismatch")
            data = path.read_bytes()
            if len(data) != row.size_bytes or hashlib.sha256(data).hexdigest() != row.sha256:
                raise ValueError("artifact integrity mismatch")
            cache[identifier] = (row, json.loads(data))
        row, payload = cache[identifier]
        if ref["sha256"] != row.sha256 or ref["bytes"] != row.size_bytes:
            raise ValueError("artifact reference mismatch")
        return payload

    def expand(value):
        if isinstance(value, dict):
            if isinstance(value.get("artifact"), dict) and "field" in value:
                return deepcopy(artifact(value["artifact"])[value["field"]])
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        return value

    for row in rows:
        if row.event_type in ("assertion.completed", "evaluation.completed"):
            continue
        wrapped = row.payload_json
        try:
            if wrapped.get("schema_version") not in ("1.1", "1.2", "1.3"):
                issues.append("legacy_untyped_event:" + row.event_uuid)
                continue
            if canonical_hash(wrapped) != row.payload_hash:
                raise ValueError("event integrity mismatch")
            payload = expand(wrapped["payload"])
            if payload["kind"] != row.event_type:
                raise ValueError("event payload kind mismatch")
            events.append(
                {
                    "event_id": row.event_uuid,
                    "type": row.event_type,
                    "run_seq": row.run_seq_no,
                    "attempt": row.attempt_no,
                    "tool_call_id": row.tool_call_id,
                    "parent_event_id": row.parent_event_uuid,
                    "payload": payload,
                }
            )
        except (ValueError, KeyError, TypeError, OSError):
            errors.append("event_or_artifact_integrity:" + row.event_uuid)
    types = {e["type"] for e in events}
    if "execution.started" not in types:
        issues.append("execution_start_missing")
    sealed = closing or execution.status in TERMINAL
    if not sealed:
        issues.append("execution_not_terminal")
    if not closing and not types.intersection(
        {"execution.completed", "execution.error", "execution.cancelled"}
    ):
        issues.append("execution_end_missing")
    requested = {e["event_id"] for e in events if e["type"] == "tool.requested"}
    completed = [e for e in events if e["type"] in ("tool.completed", "tool.error")]
    parents = {e["parent_event_id"] for e in completed}
    if requested != parents:
        issues.append("tool_request_result_pair_missing")
    if len(completed) != len(parents):
        errors.append("tool_result_parent_not_unique")
    model_starts = {e["event_id"] for e in events if e["type"] == "model.started"}
    model_ends = {
        e["parent_event_id"] for e in events if e["type"] in ("model.completed", "model.error")
    }
    if model_starts != model_ends:
        issues.append("model_observation_missing")
    for e in completed:
        req = next((x for x in events if x["event_id"] == e["parent_event_id"]), None)
        if req and (
            req["tool_call_id"] != e["tool_call_id"]
            or req["payload"]["tool"] != e["payload"]["tool"]
        ):
            errors.append("tool_pair_identity_mismatch:" + e["event_id"])
    if execution.after_state_json is None:
        issues.append("business_after_snapshot_missing")
    if execution.before_state_json is None:
        issues.append("business_before_snapshot_missing")
    if execution.final_output_json is None:
        issues.append("agent_output_missing")
    return Evidence(
        execution.execution_uuid,
        execution.before_state_json,
        execution.after_state_json,
        execution.final_output_json,
        events,
        sealed and not issues and not errors,
        issues,
        errors,
    )


def input_hash(bundle, definitions, goal):
    # Evaluation/terminal bookkeeping does not change the observations being judged.
    events = [
        e
        for e in (bundle.events or [])
        if e["type"]
        not in (
            "execution.completed",
            "execution.error",
            "execution.cancelled",
            "execution.started",
        )
    ]
    return canonical_hash(
        {
            "definitions": definitions,
            "goal": goal,
            "before": bundle.before,
            "after": bundle.after,
            "output": bundle.output,
            "events": events,
            "complete": bundle.complete,
            "issues": bundle.issues,
            "integrity_errors": bundle.integrity_errors,
        }
    )


def bounded(value):
    data = json.dumps(value, ensure_ascii=False, default=str)
    if len(data.encode()) <= 16_000:
        return value
    return {
        "truncated": True,
        "sha256": hashlib.sha256(data.encode()).hexdigest(),
        "preview": data[:3000],
        "notice": "完整观察保留在所引用的原事件/快照。",
    }


def persist_evaluation(session, run, execution, settings, *, closing=False, automatic=False):
    manifest = evaluator_manifest()
    if automatic and run.manifest_json.get("evaluation") != manifest:
        raise AgentError("evaluator_version_unavailable", "当前评价器不匹配冻结版本。", 409)
    expected = execution.input_snapshot_json["dsl"]
    definitions, goal = expected["assertions"], expected.get("task_goal")
    bundle = load_evidence(session, run, execution, closing=closing)
    digest = input_hash(bundle, definitions, goal)
    version = manifest["version"] + "-" + digest[:16]
    existing = list(
        session.scalars(
            select(AssertionResult)
            .where(
                AssertionResult.execution_id == execution.id,
                AssertionResult.evaluator_kind == "rule",
                AssertionResult.evaluator_version == version,
            )
            .order_by(AssertionResult.id)
        )
    )
    if existing:
        return summarize(existing)
    outcome = evaluate(definitions, bundle, goal=goal)
    for result in outcome["results"]:
        clean = redact(result, settings)
        row = AssertionResult(
            execution_id=execution.id,
            assertion_key=clean["key"],
            metric_name=clean["type"],
            evaluator_kind="rule",
            evaluator_version=version,
            verdict=clean["verdict"],
            expected_json=clean["expected"],
            actual_json={
                "value": bounded(clean["actual"]),
                "input_hash": digest,
                "evaluator": manifest,
                "task_outcome": outcome["task_outcome"],
            },
            evidence_refs_json=clean["evidence_refs"],
            reason=clean["reason"],
        )
        session.add(row)
        session.flush()
        # Historical manifests/events/statuses remain exactly as frozen.
        if run.manifest_json.get("trace_schema") == "1.3":
            append_event(
                session,
                run,
                execution,
                {
                    "kind": "assertion.completed",
                    "assertion_key": row.assertion_key,
                    "assertion_type": row.metric_name,
                    "evaluator_version": version,
                    "code_version": manifest["version"],
                    "verdict": row.verdict,
                    "expected": row.expected_json,
                    "actual": bounded(clean["actual"]),
                    "reason": row.reason,
                    "references": row.evidence_refs_json,
                },
                settings=settings,
                event_id=str(
                    uuid5(UUID(execution.execution_uuid), f"assert:{version}:{row.assertion_key}")
                ),
            )
    summary = {
        "verdict": outcome["verdict"],
        "counts": outcome["counts"],
        "task_outcome": outcome["task_outcome"],
        "evaluator_version": version,
        "code_version": manifest["version"],
        "input_hash": digest,
    }
    if run.manifest_json.get("trace_schema") == "1.3":
        append_event(
            session,
            run,
            execution,
            {"kind": "evaluation.completed", **summary},
            settings=settings,
            event_id=str(uuid5(UUID(execution.execution_uuid), f"evaluation:{version}")),
        )
    return summary


def summarize(rows):
    counts = {
        v: sum(r.verdict == v for r in rows)
        for v in ("passed", "failed", "inconclusive", "error", "na")
    }
    verdict = next((v for v in ("error", "failed", "inconclusive") if counts[v]), "passed")
    first = rows[0]
    return {
        "verdict": verdict,
        "counts": counts,
        "task_outcome": first.actual_json["task_outcome"],
        "evaluator_version": first.evaluator_version,
        "code_version": first.actual_json["evaluator"]["version"],
        "input_hash": first.actual_json["input_hash"],
    }


class EvaluationService:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    @staticmethod
    def owned(session, owner, identifier, *, lock=False):
        # Lock run first, then execution, matching every existing control boundary.
        run_id = session.scalar(
            select(TestExecution.run_id)
            .join(TestRun)
            .where(TestExecution.execution_uuid == str(identifier), TestRun.created_by == owner)
        )
        if run_id is None:
            raise AgentError("execution_not_found", "找不到执行。", 404)
        rq = select(TestRun).where(TestRun.id == run_id)
        eq = select(TestExecution).where(TestExecution.execution_uuid == str(identifier))
        run = session.scalar(rq.with_for_update() if lock else rq)
        execution = session.scalar(eq.with_for_update() if lock else eq)
        return run, execution

    def evaluate(self, owner, identifier):
        with Session(self.engine) as session, session.begin():
            run, execution = self.owned(session, owner, identifier, lock=True)
            if execution.status not in TERMINAL:
                raise AgentError("evaluation_not_terminal", "执行尚未结束，不提前下结论。", 409)
            return persist_evaluation(session, run, execution, self.settings)

    def read(self, owner, identifier, *, version=None, after=0, limit=100):
        with Session(self.engine) as session:
            _, execution = self.owned(session, owner, identifier)
            base = select(AssertionResult).where(
                AssertionResult.execution_id == execution.id,
                AssertionResult.evaluator_kind == "rule",
            )
            latest = session.scalar(base.order_by(AssertionResult.id.desc()).limit(1))
            version = version or (latest.evaluator_version if latest else None)
            if not version:
                return {
                    "execution_id": str(identifier),
                    "items": [],
                    "summary": None,
                    "has_more": False,
                    "next_cursor": None,
                    "versions": [],
                }
            rows = list(
                session.scalars(
                    base.where(AssertionResult.evaluator_version == version).order_by(
                        AssertionResult.id
                    )
                )
            )
            found = [r for r in rows if r.id > after]
            versions = list(
                session.scalars(
                    select(AssertionResult.evaluator_version)
                    .where(AssertionResult.execution_id == execution.id)
                    .group_by(AssertionResult.evaluator_version)
                    .order_by(func.max(AssertionResult.id).desc())
                    .limit(20)
                )
            )
            return {
                "execution_id": str(identifier),
                "items": [
                    {
                        "id": r.id,
                        "key": r.assertion_key,
                        "type": r.metric_name,
                        "verdict": r.verdict,
                        "expected": r.expected_json,
                        "actual": r.actual_json["value"],
                        "reason": r.reason,
                        "evidence_refs": r.evidence_refs_json,
                        "evaluator_version": r.evaluator_version,
                    }
                    for r in found[:limit]
                ],
                "summary": summarize(rows) if rows else None,
                "has_more": len(found) > limit,
                "next_cursor": found[limit - 1].id if len(found) > limit else None,
                "versions": versions,
            }
