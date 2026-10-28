"""Deterministic, execution-local fault receipts. A model never selects injection."""

import time
from copy import deepcopy
from uuid import UUID, uuid5

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.evidence.recorder import append_event
from app.schemas.faults import FaultSpec
from app.services.booking import BookingError


class FaultController:
    def __init__(self, control, recorder, frozen):
        self.control, self.recorder = control, recorder
        self.frozen = frozen
        self.config = FaultSpec.model_validate(frozen["config"])
        if (
            not self.config.enabled_for_test
            or canonical_hash(frozen["config"]) != frozen["config_hash"]
        ):
            raise AgentError("fault_manifest_invalid", "冻结故障清单无效。", 409)

    def _reserve(self, state, name, values, tool_call_id, message_id, parent):
        if name != self.config.target_tool:
            return None
        if not tool_call_id or not message_id or not parent:
            raise AgentError(
                "fault_call_identity_missing", "故障需要真实消息/调用/请求事件标识。", 409
            )
        key = canonical_hash(
            {
                "message": message_id,
                "call": tool_call_id,
                "tool": name,
                "arguments": values.model_dump(mode="json"),
            }
        )
        with self.control.transaction() as (session, run, execution):
            if (
                state["scope_id"] != execution.scope_id
                or not state["scope_id"].startswith("test:")
                or state["session_id"] != execution.session_id
                or state["actor_key"] != f"user:{run.created_by}"
                or run.manifest_json.get("fault_profile") != self.frozen
            ):
                raise AgentError("fault_scope_mismatch", "故障只能作用于绑定的隔离执行。", 403)
            timing = deepcopy(execution.timing_json)
            ledger = timing.setdefault(
                "faults",
                {
                    "profile_hash": self.frozen["config_hash"],
                    "eligible_calls": 0,
                    "reserved": 0,
                    "injected": 0,
                    "not_applied": 0,
                    "operations": {},
                },
            )
            old = ledger["operations"].get(key)
            if old:
                if old["state"] == "reserved":
                    raise AgentError(
                        "fault_recovery_uncertain",
                        "原注入尚无完整回执，保留事实并拒绝盲重放。",
                        409,
                    )
                return {**old, "key": key, "reused": True}
            ordinal = ledger["eligible_calls"] + 1
            ledger["eligible_calls"] = ordinal
            decision = canonical_hash(
                {
                    "profile": self.frozen["config_hash"],
                    "profile_seed": self.config.seed,
                    "execution_seed": execution.seed,
                }
            )
            first = (
                self.config.parameters.trigger_on_nth
                + int(decision[:16], 16) % self.config.parameters.trigger_window
            )
            selected = first <= ordinal < first + self.config.max_triggers
            op = {
                "state": "reserved" if selected else "bypass",
                "ordinal": ordinal,
                "selected": selected,
                "trigger_no": ordinal - first + 1 if selected else None,
                "decision_hash": decision,
                "before": None,
                "reused": False,
            }
            if selected:
                from app.executors.service import booking_snapshot

                op["before"] = booking_snapshot(session, execution.scope_id)
                ledger["reserved"] += 1
                append_event(
                    session,
                    run,
                    execution,
                    self._payload("fault.scheduled", op, execution.seed, dispatched=False),
                    settings=self.recorder.settings,
                    event_id=str(uuid5(UUID(execution.execution_uuid), f"fault:{key}:scheduled")),
                    node="tools",
                    tool_call_id=tool_call_id,
                    parent=parent,
                )
            ledger["operations"][key] = op
            execution.timing_json = timing
            return {**op, "key": key}

    def _payload(self, kind, op, execution_seed, **extra):
        return {
            "kind": kind,
            "fault_type": self.config.fault_type,
            "stage": self.config.trigger_stage,
            "profile_id": self.frozen["id"],
            "profile_revision": self.config.revision,
            "profile_hash": self.frozen["config_hash"],
            "tool": self.config.target_tool,
            "eligible_call_no": op["ordinal"],
            "trigger_no": op["trigger_no"],
            "max_triggers": self.config.max_triggers,
            "seed": self.config.seed,
            "execution_seed": execution_seed,
            "decision_hash": op["decision_hash"],
            "before": op["before"],
            **extra,
        }

    @staticmethod
    def _mask(state, previous, fault_type, values):
        if fault_type in ("F03", "F04"):
            state.clear()
            state.update(deepcopy(previous))
        if fault_type == "F04":
            state.update(
                business_result=None,
                grant=None,
                confirmation_state="unknown",
                write_attempted_this_turn=True,
                unknown_submission={"request_key": str(values.idempotency_key)},
            )
        elif fault_type in ("F01", "F02") and "idempotency_key" in type(values).model_fields:
            state.update(grant=None, confirmation_state="pending", write_attempted_this_turn=True)

    def invoke(self, state, name, values, call, *, tool_call_id, message_id, parent):
        op = self._reserve(state, name, values, tool_call_id, message_id, parent)
        if not op or not op["selected"]:
            raw = call()
            return {"raw_result": raw, "visible_result": raw, "dispatched": True}
        previous = deepcopy(state)
        fault_type = self.config.fault_type
        if op.get("reused"):
            if op["state"] == "injected":
                self._mask(state, previous, fault_type, values)
            return {
                "raw_result": None,
                "visible_result": op["visible_result"],
                "dispatched": False,
                "receipt_reused": True,
                "fault_event_id": op["event_id"],
            }
        raw, rollback_probe = None, None
        dispatched, applied = fault_type != "F01", True
        if fault_type == "F01":
            time.sleep(self.config.parameters.delay_ms / 1000)
            self.control.check()
            visible = {
                "status": "error",
                "code": "tool_timeout",
                "http_status": 504,
                "message": "工具调用超时；服务端确认未执行。",
                "commit_state": "not_committed",
                "retryable": False,
            }
            if name == "confirm_booking":
                visible["request_key"] = str(values.idempotency_key)
        elif fault_type == "F02":

            def fail_before_commit(session, result):
                nonlocal rollback_probe
                from app.executors.service import booking_snapshot

                rollback_probe = booking_snapshot(session, state["scope_id"])
                raise BookingError(
                    "upstream_unavailable",
                    "业务服务明确返回503，本次事务已回滚。",
                    503,
                    commit_state="not_committed",
                    request_key=values.idempotency_key,
                )

            raw = call(before_commit=fail_before_commit)
            visible, applied = raw, rollback_probe is not None
        else:
            raw = call()
            if fault_type == "F03":
                variants = ['{"data": [', '{"status":"ok", "data":', '{"chunks": [broken']
                visible = variants[int(op["decision_hash"][-8:], 16) % len(variants)]
            else:
                applied = isinstance(raw, dict) and raw.get("commit_state") == "committed"
                visible = (
                    {
                        "status": "unknown",
                        "code": "response_lost",
                        "http_status": 504,
                        "message": "提交响应丢失，状态未知；请用原请求标识查证，不能换键提交。",
                        "commit_state": "unknown",
                        "retryable": False,
                        "request_key": str(values.idempotency_key),
                    }
                    if applied
                    else raw
                )
        if applied:
            self._mask(state, previous, fault_type, values)
        with self.control.transaction() as (session, run, execution):
            from app.executors.service import booking_snapshot

            after = booking_snapshot(session, execution.scope_id)
            actual_commit = (
                "committed"
                if fault_type == "F04" and applied
                else (
                    "not_committed"
                    if fault_type in ("F01", "F02") and applied
                    else "not_applicable"
                )
            )
            row = append_event(
                session,
                run,
                execution,
                self._payload(
                    "fault.injected" if applied else "fault.not_applied",
                    op,
                    execution.seed,
                    dispatched=dispatched,
                    raw_result=raw,
                    visible_result=visible,
                    after=after,
                    rollback_probe=rollback_probe,
                    commit_state=actual_commit,
                    reason=None if applied else "original_tool_did_not_reach_required_stage",
                ),
                settings=self.recorder.settings,
                event_id=str(uuid5(UUID(execution.execution_uuid), f"fault:{op['key']}:resolved")),
                node="tools",
                tool_call_id=tool_call_id,
                parent=parent,
            )
            timing = deepcopy(execution.timing_json)
            ledger = timing["faults"]
            status = "injected" if applied else "not_applied"
            ledger[status] += 1
            ledger["operations"][op["key"]].update(
                state=status, event_id=row.event_uuid, visible_result=visible, dispatched=dispatched
            )
            execution.timing_json = timing
            return {
                "raw_result": raw,
                "visible_result": visible,
                "dispatched": dispatched,
                "fault_event_id": row.event_uuid,
                "receipt_reused": False,
            }
