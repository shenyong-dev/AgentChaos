"""Execution port for the reference agent; reuses existing leases, evidence and budgets."""

import json
import time
from uuid import UUID, uuid5

from langchain_core.messages import messages_from_dict, messages_to_dict

from app.adapters.refund import RefundAdapter
from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.faults.engine import FaultController
from reference_agents.refund.agent import RefundAgent


def restore_checkpoint(saved, timing, claim):
    revision = saved.get("committed_revision")
    pending = timing.get("pending_input")
    registered_input = (
        isinstance(pending, dict)
        and pending.get("expected_revision") == revision
        and isinstance(pending.get("request_id"), str)
        and timing.get("input_receipts", {}).get(pending["request_id"]) == canonical_hash(pending)
    )
    expected_revision = revision + int(registered_input) if type(revision) is int else None
    if (
        saved.get("cursor") != timing.get("cursor", 0)
        or expected_revision != timing.get("revision", 0)
        or saved.get("binding") != claim["manifest"]["target"]["config_hash"]
        or saved.get("owner") != claim["owner_id"]
        or saved.get("scope") != claim["scope_id"]
    ):
        raise AgentError("checkpoint_unavailable", "退款检查点与原执行不匹配。", 409)
    return saved


def execute(engine, redis, settings, claim, control, recorder, gateway):
    if claim["recovery"]:
        raise AgentError(
            "recovery_uncertain", "参考退款对象不自动重放崩溃中的调用，请核对原请求账本。", 409
        )
    adapter = RefundAdapter(engine, settings)
    if adapter.manifest()["config_hash"] != claim["manifest"]["target"]["config_hash"]:
        raise AgentError("target_version_unavailable", "退款对象实现不匹配冻结版本。", 409)
    faults = (
        FaultController(control, recorder, claim["manifest"]["fault_profile"])
        if claim["manifest"].get("fault_profile")
        else None
    )
    agent = RefundAgent(
        adapter.store(claim["scope_id"], claim["owner_id"]),
        gateway,
        recorder,
        control,
        {
            "scope_id": claim["scope_id"],
            "session_id": claim["session_id"],
            "actor_key": f"user:{claim['owner_id']}",
        },
        faults,
    )
    key = settings.executor_namespace + ":refund:checkpoint:" + claim["session_id"]
    with control.transaction() as (_, _, execution):
        timing = dict(execution.timing_json)
    cursor = timing.get("cursor", 0)
    saved = redis.get(key)
    if cursor:
        if not saved:
            raise AgentError("checkpoint_unavailable", "退款检查点缺失，不重放历史输入。", 409)
        saved = restore_checkpoint(json.loads(saved), timing, claim)
        agent.messages = messages_from_dict(saved["messages"])
        agent.state, agent.trace = saved["state"], saved["trace"]
        recorder.emit("checkpoint.restored", checkpoint_id=canonical_hash(saved), next_nodes=[])
    elif saved:
        raise AgentError("recovery_uncertain", "检查点超前于SQL游标，不自动重放。", 409)
    turns = claim["case"]["dsl"]["turns"]
    output = None
    supplied = timing.get("pending_input")
    inputs = [(i, t["user"], "script") for i, t in enumerate(turns) if i >= cursor]
    if supplied:
        inputs.append((cursor, supplied["text"], "input"))
    for index, text, source in inputs:
        control.check()
        request = (
            supplied["request_id"]
            if source == "input"
            else str(uuid5(UUID(claim["session_id"]), f"turn:{index}"))
        )
        recorder.emit(
            "input.received",
            request_id=request,
            source=source,
            text=text,
            expected_revision=agent.state["revision"],
        )
        output = agent.turn(text, deadline=time.monotonic() + 120)
        cursor += int(source == "script")
        checkpoint = {
            "binding": claim["manifest"]["target"]["config_hash"],
            "owner": claim["owner_id"],
            "scope": claim["scope_id"],
            "cursor": cursor,
            "committed_revision": timing.get("revision", 0) + 1,
            "state": agent.state,
            "trace": agent.trace,
            "messages": messages_to_dict(agent.messages),
        }
        redis.set(key, json.dumps(checkpoint, ensure_ascii=False), ex=86400)
        with control.transaction() as (_, _, execution):
            timing = dict(execution.timing_json)
            timing.update(cursor=cursor, revision=timing.get("revision", 0) + 1)
            timing.pop("pending_input", None)
            execution.timing_json, execution.final_output_json = timing, output
    if output is None:
        raise AgentError("refund_input_missing", "没有可安全继续的输入。", 409)
    return output
