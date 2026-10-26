"""Persistent run plans and leases. Broker messages carry only an execution UUID."""

from copy import deepcopy
from datetime import datetime, timedelta
from socket import gethostname
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.agents.budget import PRICING
from app.agents.errors import AgentError
from app.agents.versions import build_manifest, canonical_hash
from app.db.fixtures import ensure_rooms
from app.evidence.recorder import append_event
from app.executors.control import TERMINAL, advisory, database_now, locked_pair
from app.models import Booking, Target, TargetVersion, TestCase, TestExecution, TestRun
from app.schemas.contracts import TestCaseDraft


def case_content(row):
    return {
        "case_key": row.case_key,
        "revision": row.revision,
        "family_key": row.family_key,
        "category": row.category,
        "dataset_split": row.dataset_split,
        "source": row.source,
        "turns": row.turns_json,
        "fixture": row.fixture_json,
        "expected": row.expected_json,
    }


def role_configs(settings):
    return {
        role: {
            "provider": "DeepSeek",
            "base_url": settings.deepseek_base_url,
            "model": getattr(settings, model),
            "thinking": getattr(settings, thinking),
            "max_output_tokens": getattr(settings, output),
            "timeout_seconds": settings.model_timeout_seconds,
            "automatic_retries": 0,
        }
        for role, model, thinking, output in (
            ("agent", "agent_model", "agent_thinking", "agent_max_output_tokens"),
            ("case_generator", "casegen_model", "casegen_thinking", "casegen_max_output_tokens"),
            ("judge", "judge_model", "judge_thinking", "judge_max_output_tokens"),
        )
    }


def frozen_case(row):
    content = case_content(row)
    if row.content_hash != canonical_hash(content):
        raise AgentError("case_hash_mismatch", "用例修订的内容哈希不一致。", 409)
    try:
        draft = TestCaseDraft.model_validate(
            {
                **{
                    k: content[k]
                    for k in (
                        "case_key",
                        "revision",
                        "family_key",
                        "category",
                        "dataset_split",
                        "turns",
                    )
                },
                "reference_clock": content["fixture"]["reference_clock"],
                "fixture": content["fixture"]["fixture"],
                "assertions": content["expected"]["assertions"],
                "review_status": "approved",
            }
        )
    except (ValueError, KeyError):
        raise AgentError("case_invalid", "已审核用例不满足当前DSL契约。", 409) from None
    if draft.fixture != "booking-default-v1":
        raise AgentError("fixture_not_supported", "当前执行器只支持已实现的预约基础夹具。", 400)
    return {
        "case_revision_id": row.id,
        "content_hash": row.content_hash,
        "content": content,
        "dsl": draft.model_dump(mode="json"),
        "reviewer_id": row.reviewer_id,
        "reviewed_at": row.reviewed_at.isoformat(),
    }


def booking_snapshot(session, scope):
    return [
        {
            "booking_id": row.booking_uuid,
            "request_key": row.idempotency_key,
            "room_id": row.room_id,
            "start_at": row.start_at.isoformat(),
            "end_at": row.end_at.isoformat(),
            "attendees": row.attendees,
            "status": row.status,
        }
        for row in session.scalars(
            select(Booking).where(Booking.scope_id == scope).with_for_update()
        )
    ]


def refresh_run(session, run):
    session.flush()
    statuses = list(
        session.scalars(
            select(TestExecution.status).where(TestExecution.run_id == run.id).with_for_update()
        )
    )
    summary = dict(run.summary_json or {})
    summary["counts"] = {status: statuses.count(status) for status in sorted(set(statuses))}
    run.summary_json = summary
    if (
        run.status not in ("completed", "failed", "cancelled")
        and statuses
        and all(status in TERMINAL for status in statuses)
    ):
        run.status = "cancelled" if run.status == "cancelling" else "completed"
        run.finished_at = database_now(session)
        append_event(
            session,
            run,
            None,
            {"kind": "run.completed", "status": run.status, "counts": summary["counts"]},
        )


class RunService:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    def current_manifest(self):
        from app.agents.registry import ToolRegistry
        from app.agents.retrieval import KnowledgeRetriever
        from app.services.booking import BookingService

        retriever = KnowledgeRetriever(self.engine)
        return build_manifest(
            self.settings, retriever, ToolRegistry(BookingService(self.engine), retriever)
        )

    def create(self, user_id, values):
        request = values.model_dump(mode="json")
        request_hash = canonical_hash(request)
        with advisory(self.engine, "create:" + str(values.request_id)) as connection:
            with Session(connection) as session, session.begin():
                existing = session.scalar(
                    select(TestRun).where(TestRun.run_uuid == str(values.request_id))
                )
                if existing:
                    if existing.created_by != user_id:
                        raise AgentError("run_not_found", "找不到运行。", 404)
                    if existing.manifest_json["request_hash"] != request_hash:
                        raise AgentError(
                            "run_request_conflict", "相同请求标识的运行配置不同。", 409
                        )
                    return existing.run_uuid
                version = session.get(TargetVersion, values.target_version_id)
                target = session.get(Target, version.target_id) if version else None
                if (
                    not target
                    or target.owner_id != user_id
                    or target.adapter_type != "langgraph_booking"
                ):
                    raise AgentError(
                        "target_version_not_found", "当前身份下找不到预约对象版本。", 404
                    )
                current = self.current_manifest()
                if version.config_hash != current["config_hash"]:
                    raise AgentError(
                        "target_version_unavailable", "当前Worker代码不能执行该历史版本。", 409
                    )
                rows = list(
                    session.scalars(
                        select(TestCase)
                        .where(TestCase.id.in_(values.case_revision_ids))
                        .order_by(TestCase.id)
                    )
                )
                if len(rows) != len(values.case_revision_ids) or any(
                    r.review_status != "approved" for r in rows
                ):
                    raise AgentError("case_not_approved", "运行只接受存在且已审核的用例修订。", 409)
                cases = [frozen_case(row) for row in rows]
                s = self.settings
                if not s.executor_agent_budget_unlimited and values.budgets.agent_microusd > int(
                    s.executor_agent_budget_usd * 1_000_000
                ):
                    raise AgentError(
                        "run_budget_not_authorized", "运行预算超出本地已批准上限。", 403
                    )
                limits = values.limits.model_dump()
                limits["concurrency"] = min(limits["concurrency"], s.run_concurrency)
                manifest = {
                    "schema": "run-v1",
                    "executor": "T05-v1",
                    "trace_schema": "1.1",
                    "request_hash": request_hash,
                    "target_version_id": version.id,
                    "target": version.model_config_json,
                    "cases": cases,
                    "limits": limits,
                    "repetitions": values.repetitions,
                    "seed": values.seed,
                    "fault_profile": None,
                    "roles": role_configs(s),
                    "pricing": {**PRICING, "verified_on": "2026-10-26"},
                    "evaluation": "T08 not implemented; script completion is inconclusive",
                }
                budget = {
                    "currency": "USD",
                    "unit": "microUSD",
                    "authorization_id": s.executor_budget_authorization_id,
                    "authorization_unlimited": s.executor_agent_budget_unlimited,
                    **values.budgets.model_dump(),
                }
                run = TestRun(
                    run_uuid=str(values.request_id),
                    created_by=user_id,
                    target_version_id=version.id,
                    manifest_json=manifest,
                    manifest_hash=canonical_hash(manifest),
                    budget_json=budget,
                    summary_json={"ledger": {}, "evaluation": "not_implemented"},
                )
                session.add(run)
                session.flush()
                for case in cases:
                    for repeat in range(1, values.repetitions + 1):
                        identifier = str(uuid4())
                        session.add(
                            TestExecution(
                                execution_uuid=identifier,
                                run_id=run.id,
                                case_revision_id=case["case_revision_id"],
                                repeat_no=repeat,
                                seed=values.seed + repeat - 1,
                                scope_id="test:" + identifier,
                                session_id=str(uuid4()),
                                input_snapshot_json=deepcopy(case),
                                timing_json={
                                    "cursor": 0,
                                    "steps": 0,
                                    "revision": 0,
                                    "dispatch_count": 0,
                                },
                                usage_json={"tickets": {}, "roles": {}},
                                attempt_history_json=[],
                            )
                        )
                append_event(
                    session, run, None, {"kind": "run.created", "status": "queued"}, settings=s
                )
                return run.run_uuid

    @staticmethod
    def owned_run(session, user_id, run_uuid, *, lock=False):
        query = select(TestRun).where(
            TestRun.run_uuid == str(run_uuid), TestRun.created_by == user_id
        )
        run = session.scalar(query.with_for_update() if lock else query)
        if run is None:
            raise AgentError("run_not_found", "当前身份下找不到运行。", 404)
        return run

    def read(self, user_id, run_uuid):
        with Session(self.engine) as session:
            run = self.owned_run(session, user_id, run_uuid)
            executions = list(
                session.scalars(
                    select(TestExecution)
                    .where(TestExecution.run_id == run.id)
                    .order_by(TestExecution.id)
                )
            )
            return {
                "run_id": run.run_uuid,
                "status": run.status,
                "manifest_hash": run.manifest_hash,
                "manifest": run.manifest_json,
                "budget": run.budget_json,
                "summary": run.summary_json,
                "executions": [self.execution_view(e) for e in executions],
            }

    @staticmethod
    def execution_view(e):
        return {
            "execution_id": e.execution_uuid,
            "status": e.status,
            "attempt": e.attempt_no,
            "case_revision_id": e.case_revision_id,
            "repeat_no": e.repeat_no,
            "scope_id": e.scope_id,
            "session_id": e.session_id,
            "attempt_history": e.attempt_history_json,
            "timing": e.timing_json,
            "usage": e.usage_json,
            "error": e.error_json,
            "output": e.final_output_json,
            "before": e.before_state_json,
            "after": e.after_state_json,
            "task_outcome": e.task_outcome,
        }

    def cancel(self, user_id, run_uuid):
        with Session(self.engine) as session, session.begin():
            run = self.owned_run(session, user_id, run_uuid, lock=True)
            if run.status in ("completed", "failed", "cancelled"):
                return
            run.status = "cancelling"
            append_event(
                session,
                run,
                None,
                {"kind": "run.cancel_requested", "status": "cancelling"},
                settings=self.settings,
            )
            rows = list(
                session.scalars(
                    select(TestExecution).where(TestExecution.run_id == run.id).with_for_update()
                )
            )
            for row in rows:
                # Invalidate tokens immediately, including in-flight model/checkpoint work.
                if row.status not in TERMINAL:
                    row.status, row.lease_token, row.lease_until = "cancelled", None, None
                    row.error_json = {"category": "control", "code": "execution_cancelled"}
                    row.after_state_json = booking_snapshot(session, row.scope_id)
                    append_event(
                        session,
                        run,
                        row,
                        {
                            "kind": "execution.cancelled",
                            "status": "cancelled",
                            "reason": "execution_cancelled",
                            "after": row.after_state_json,
                            "usage": row.usage_json,
                        },
                        settings=self.settings,
                    )
            refresh_run(session, run)

    def supply_input(self, user_id, run_uuid, execution_uuid, values):
        body = values.model_dump(mode="json")
        with Session(self.engine) as session, session.begin():
            run = self.owned_run(session, user_id, run_uuid, lock=True)
            e = session.scalar(
                select(TestExecution)
                .where(
                    TestExecution.run_id == run.id,
                    TestExecution.execution_uuid == str(execution_uuid),
                )
                .with_for_update()
            )
            if e is None:
                raise AgentError("execution_not_found", "找不到执行。", 404)
            timing = dict(e.timing_json or {})
            receipts = dict(timing.get("input_receipts", {}))
            identifier, digest = body["request_id"], canonical_hash(body)
            if identifier in receipts:
                if receipts[identifier] != digest:
                    raise AgentError("input_request_conflict", "同一输入标识内容不一致。", 409)
                return
            if (
                run.status != "running"
                or e.status != "waiting_input"
                or timing["revision"] != values.expected_revision
            ):
                raise AgentError("execution_not_waiting", "执行未等待此版本的合法输入。", 409)
            receipts[identifier] = digest
            timing.update(
                input_receipts=receipts, pending_input=body, revision=timing["revision"] + 1
            )
            e.timing_json, e.status = timing, "queued"

    def candidates(self):
        with Session(self.engine) as session:
            now = database_now(session)
            return list(
                session.scalars(
                    select(TestExecution.execution_uuid)
                    .where(
                        or_(
                            TestExecution.status == "queued",
                            (TestExecution.status == "running") & (TestExecution.lease_until < now),
                        )
                    )
                    .order_by(TestExecution.id)
                    .limit(500)
                )
            )

    def expire_deadlines(self):
        """Waiting for input releases capacity, but does not suspend wall-clock limits."""
        with Session(self.engine) as session:
            ids = list(
                session.scalars(
                    select(TestExecution.id)
                    .join(TestRun)
                    .where(
                        TestRun.status.in_(("queued", "running")),
                        TestExecution.status.in_(("queued", "running", "waiting_input")),
                    )
                    .order_by(TestExecution.id)
                )
            )
        expired = 0
        for identifier in ids:
            with Session(self.engine) as session, session.begin():
                run, e = locked_pair(session, identifier)
                deadline = (e.timing_json or {}).get("deadline")
                now = database_now(session)
                if (
                    run.status not in ("queued", "running")
                    or e.status in TERMINAL
                    or not deadline
                    or now < datetime.fromisoformat(deadline)
                ):
                    continue
                e.status, e.lease_token, e.lease_until = "error", None, None
                e.error_json = {"category": "limit", "code": "execution_timeout"}
                e.timing_json = {**e.timing_json, "finished_at": now.isoformat()}
                e.after_state_json = booking_snapshot(session, e.scope_id)
                append_event(
                    session,
                    run,
                    e,
                    {
                        "kind": "execution.error",
                        "status": "error",
                        "reason": "execution_timeout",
                        "after": e.after_state_json,
                        "usage": e.usage_json,
                    },
                    settings=self.settings,
                )
                refresh_run(session, run)
                expired += 1
        return expired

    def recover(self, user_id, run_uuid, execution_uuid, values):
        """Explicitly retry a persisted graph node, never recreate a scope/session."""
        with Session(self.engine) as session, session.begin():
            run = self.owned_run(session, user_id, run_uuid, lock=True)
            e = session.scalar(
                select(TestExecution)
                .where(
                    TestExecution.run_id == run.id,
                    TestExecution.execution_uuid == str(execution_uuid),
                )
                .with_for_update()
            )
            if e is None:
                raise AgentError("execution_not_found", "找不到执行。", 404)
            timing = dict(e.timing_json)
            receipt = {"id": str(values.request_id), "revision": values.expected_revision}
            if receipt in timing.get("recovery_receipts", []):
                return
            if (
                e.status != "error"
                or e.attempt_no >= 3
                or timing["revision"] != values.expected_revision
                or run.status in ("cancelled", "cancelling")
            ):
                raise AgentError(
                    "recovery_not_allowed", "此状态/版本不允许恢复，或已达三次attempt上限。", 409
                )
            if timing.get("deadline") and database_now(session) >= datetime.fromisoformat(
                timing["deadline"]
            ):
                raise AgentError("execution_timeout", "原执行已超时，恢复不能重置期限。", 409)
            original = timing.pop("inflight", None)
            if not original:
                raise AgentError("recovery_uncertain", "没有可核对的原始在途输入。", 409)
            timing.update(
                manual_recovery=True,
                recovery_source=original["source"],
                revision=timing["revision"] + 1,
                dispatch_at=None,
                recovery_receipts=timing.get("recovery_receipts", []) + [receipt],
            )
            e.attempt_history_json = e.attempt_history_json + [
                {
                    "attempt": e.attempt_no,
                    "reason": "explicit_node_recovery",
                    "error": e.error_json,
                    "at": database_now(session).isoformat(),
                }
            ]
            e.attempt_no += 1
            e.status, e.error_json, e.timing_json = "queued", None, timing
            run.status, run.finished_at = "running", None
            append_event(
                session,
                run,
                None,
                {"kind": "run.resumed", "status": "running", "reason": "explicit_node_recovery"},
                settings=self.settings,
            )

    def claim(self, execution_uuid):
        # Serializes capacity decisions across runs; no distributed semaphore leak on crash.
        with advisory(self.engine, "claim") as connection:
            with Session(connection) as session, session.begin():
                identifier = session.scalar(
                    select(TestExecution.id).where(
                        TestExecution.execution_uuid == str(execution_uuid)
                    )
                )
                if identifier is None:
                    return None
                run, e = locked_pair(session, identifier)
                now = database_now(session)
                if run.status not in ("queued", "running") or e.status not in ("queued", "running"):
                    return None
                if e.status == "running" and e.lease_until and e.lease_until > now:
                    return None
                valid = [TestExecution.status == "running", TestExecution.lease_until > now]
                total = session.scalar(
                    select(func.count()).select_from(TestExecution).where(*valid)
                )
                count = session.scalar(
                    select(func.count())
                    .select_from(TestExecution)
                    .where(*valid, TestExecution.run_id == run.id)
                )
                if (
                    total >= self.settings.run_concurrency
                    or count >= run.manifest_json["limits"]["concurrency"]
                ):
                    return None
                timing = dict(e.timing_json or {})
                if timing.get("deadline") and now >= datetime.fromisoformat(timing["deadline"]):
                    e.status, e.error_json = (
                        "error",
                        {"category": "limit", "code": "execution_timeout"},
                    )
                    e.lease_token, e.lease_until = None, None
                    e.after_state_json = booking_snapshot(session, e.scope_id)
                    append_event(
                        session,
                        run,
                        e,
                        {
                            "kind": "execution.error",
                            "status": "error",
                            "reason": "execution_timeout",
                            "after": e.after_state_json,
                            "usage": e.usage_json,
                        },
                        settings=self.settings,
                    )
                    refresh_run(session, run)
                    return None
                history = list(e.attempt_history_json)
                recovery = bool(e.lease_token)
                previous_token = e.lease_token
                if recovery:
                    history.append(
                        {
                            "attempt": e.attempt_no,
                            "reason": "lease_expired",
                            "at": now.isoformat(),
                            "cursor": timing.get("cursor", 0),
                        }
                    )
                    e.attempt_no += 1
                token = str(uuid4())
                e.status, e.lease_token = "running", token
                e.lease_until = now + timedelta(seconds=self.settings.executor_lease_seconds)
                timing.setdefault(
                    "deadline",
                    (
                        now + timedelta(seconds=run.manifest_json["limits"]["timeout_seconds"])
                    ).isoformat(),
                )
                timing.update(
                    claimed_at=now.isoformat(),
                    worker_attempt=e.attempt_no,
                    worker_host=gethostname(),
                )
                e.timing_json, e.attempt_history_json = timing, history
                first_start = run.started_at is None
                run.status, run.started_at = "running", run.started_at or now
                if first_start:
                    append_event(
                        session,
                        run,
                        None,
                        {"kind": "run.started", "status": "running"},
                        settings=self.settings,
                    )
                ensure_rooms(session, e.scope_id)
                if e.before_state_json is None:
                    e.before_state_json = booking_snapshot(session, e.scope_id)
                append_event(
                    session,
                    run,
                    e,
                    {
                        "kind": "execution.started",
                        "status": "running",
                        "attempt": e.attempt_no,
                        "before": e.before_state_json,
                        "reason": "lease_recovery" if recovery else "claimed",
                    },
                    settings=self.settings,
                )
                session.flush()
                return {
                    "id": e.id,
                    "token": token,
                    "previous_token": previous_token,
                    "recovery": recovery,
                    "owner_id": run.created_by,
                    "session_id": e.session_id,
                    "scope_id": e.scope_id,
                    "case": deepcopy(e.input_snapshot_json),
                    "manifest": deepcopy(run.manifest_json),
                    "run_uuid": run.run_uuid,
                    "attempt": e.attempt_no,
                }

    def heartbeat(self, execution_id, token):
        from app.executors.control import fence

        with Session(self.engine) as session, session.begin():
            _, e = fence(session, execution_id, token)
            now = database_now(session)
            e.lease_until = now + timedelta(seconds=self.settings.executor_lease_seconds)
            e.timing_json = {**e.timing_json, "heartbeat_at": now.isoformat()}

    def finish(self, execution_id, token, status, output=None, error=None):
        with Session(self.engine) as session, session.begin():
            run, e = locked_pair(session, execution_id)
            # Finalization cannot replace a newer lease, even if an old task returns late.
            if e.lease_token != token or e.status != "running":
                return False
            if not e.lease_until or e.lease_until <= database_now(session):
                return False
            e.status, e.final_output_json, e.error_json = status, output, error
            e.task_outcome = "unknown" if status != "waiting_input" else None
            e.after_state_json = booking_snapshot(session, e.scope_id)
            e.timing_json = {**e.timing_json, "finished_at": database_now(session).isoformat()}
            e.lease_token, e.lease_until = None, None
            kind = (
                "execution.waiting_input"
                if status == "waiting_input"
                else ("execution.error" if status == "error" else "execution.completed")
            )
            append_event(
                session,
                run,
                e,
                {
                    "kind": kind,
                    "status": status,
                    "reason": (error or {}).get("code"),
                    "after": e.after_state_json,
                    "usage": e.usage_json,
                    "output": output,
                },
                settings=self.settings,
            )
            if (error or {}).get("code") in ("model_budget_exhausted", "execution_step_limit"):
                append_event(
                    session,
                    run,
                    e,
                    {"kind": "budget.exceeded", "code": error["code"], "usage": e.usage_json},
                    settings=self.settings,
                )
            refresh_run(session, run)
            return True
