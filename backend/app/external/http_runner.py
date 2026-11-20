"""Run the mapped HTTP adapter under the existing execution lease and budget ledger."""

from copy import deepcopy
from uuid import UUID, uuid5

from redis.exceptions import RedisError

from app.agents.errors import AgentError
from app.external import otel
from app.external.http_port import HttpPort
from app.external.runner import Calls, execution_state, request


def trace_context(redis, settings, owner, entry, identifier, names):
    try:
        return otel.begin(redis, settings, owner, entry, identifier, names)
    except (AgentError, RedisError):
        return None, None


def execute(engine, redis, settings, claim, control, recorder, adapter):
    owner, entry = claim["owner_id"], adapter.entry
    binding, timing, previous_output, deadline = execution_state(engine, claim, control, adapter)
    calls = Calls(control, entry, settings, owner=owner)
    port = HttpPort(entry, settings, owner)
    spec = claim["case"]["dsl"]
    total = len(spec["turns"])
    cursor = timing.get("cursor", 0)
    previous_output = previous_output or {}
    if (
        claim["recovery"]
        and "external_evidence" in previous_output
        and (cursor == total or previous_output.get("status") == "receipt_unavailable")
    ):
        return previous_output
    evidence = deepcopy(previous_output.get("external_evidence")) or {
        "last_turn": total,
        "rounds": [],
        "business": {
            "before": {"status": "unavailable"},
            "after": {"status": "unavailable"},
        },
        "transport": {"status": "unknown", "reason": "等待 HTTP 回执。"},
    }
    turns = spec["turns"]
    conversation = timing.get("external_conversation")
    history = []
    if entry.http.history_field:
        for row in evidence["rounds"]:
            history += [
                {"role": "user", "content": turns[row["turn"] - 1]["user"]},
                {"role": "assistant", "content": row["assistant_text"]},
            ]
    for index in range(cursor, total):
        turn = index + 1
        last = turn == total
        identifier = str(uuid5(UUID(claim["session_id"]), f"turn:{index}"))
        message = turns[index]["user"]
        original = request(
            entry, binding, "execute", identifier, {"text": message, "expected_revision": index}
        )
        control.step("external.http")
        intent = calls.reserve(original)
        if last and intent["_fresh"]:
            control.check()
            evidence["business"]["before"] = capture_fact(
                port, binding, identifier, deadline, recorder, "before"
            )
            with control.transaction() as (_, _, execution):
                execution.before_state_json = evidence["business"]["before"]
                execution.final_output_json = {"status": "working", "external_evidence": evidence}
        if (
            last
            and not intent["_fresh"]
            and evidence["business"]["before"]["status"] == "unavailable"
        ):
            with control.transaction() as (_, _, execution):
                if execution.before_state_json:
                    evidence["business"]["before"] = execution.before_state_json
        output = perform(
            intent,
            original,
            entry,
            port,
            settings,
            redis,
            owner,
            control,
            calls,
            recorder,
            spec,
            turn,
            total,
            conversation,
            history,
            deadline,
            fault=claim["manifest"].get("external_fault") if last else None,
        )
        evidence["rounds"].append(output["round"])
        evidence["transport"] = output["transport"]
        save_after = last or output["response"] is None
        if last:
            control.check()
            evidence["business"]["after"] = capture_fact(
                port, binding, identifier, deadline, recorder, "after"
            )
        response = output["response"]
        if response:
            conversation = response["conversation_id"]
            if entry.http.history_field:
                history += [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": response["assistant_text"]},
                ]
        output = {
            "status": "completed" if response else "receipt_unavailable",
            "assistant_text": response["assistant_text"] if response else None,
            "external_evidence": evidence,
        }
        with control.transaction() as (_, _, execution):
            current = dict(execution.timing_json)
            current.update(
                cursor=turn,
                external_conversation=conversation,
                revision=current.get("revision", 0) + 1,
            )
            execution.timing_json, execution.final_output_json = current, output
        if save_after:
            return output
    raise AgentError("external_input_missing", "没有可继续的测试消息。", 409)


def perform(
    intent,
    original,
    entry,
    port,
    settings,
    redis,
    owner,
    control,
    calls,
    recorder,
    spec,
    turn,
    total,
    conversation,
    history,
    deadline,
    *,
    fault,
):
    identifier = original["request_id"]
    required = [
        a
        for a in spec["assertions"]
        if a["type"] in {"external_tool_sequence", "external_retrieval_top_k"}
        and (a.get("turn") or total) == turn
    ]
    arguments = {key for a in required for step in a.get("steps", []) for key in step["arguments"]}
    trace_id, traceparent = trace_context(redis, settings, owner, entry, identifier, arguments)
    recorder.emit(
        "input.received",
        request_id=identifier,
        source="script",
        text=original["data"]["text"],
        expected_revision=turn - 1,
    )
    response = intent["response"] if intent["state"] == "received" else None
    transport = {"status": "received", "request_id": identifier, "reason": "已收到 HTTP 回执。"}
    if intent["_fresh"]:
        control.check()
        try:
            response = port.send(
                original,
                conversation,
                history,
                traceparent,
                deadline=deadline,
                tool_fault=fault == "tool_error",
            )
        except AgentError as exc:
            transport = {
                "status": "unknown",
                "request_id": identifier,
                "reason": exc.message,
                "code": exc.code,
            }
            calls.record(original)
        else:
            if fault == "response_timeout":
                # The response is deliberately discarded at the client boundary. Keep the
                # original ID, settle the observed send, and never send another POST.
                calls.record(original, transport_received=True)
                response = None
                transport.update(status="receipt_timeout", reason="故障组已模拟回执超时。")
            else:
                calls.record(original, response)
    elif response is None:
        transport.update(status="unknown", reason="检测到已保存的原请求，等待只读查证。")
    control.check()
    trace = otel.collect(
        redis,
        settings,
        trace_id,
        deadline,
        wait=fault == "tool_error"
        or any(a["type"] == "external_tool_sequence" for a in required)
        or bool(required and not (response and response["retrieved_documents"] is not None)),
    )
    docs = response["retrieved_documents"] if response else None
    origin = "agent_response" if docs is not None else None
    if docs is None and trace and trace["complete"] and trace["documents"] is not None:
        docs, origin = trace["documents"], "opentelemetry"
    round_view = {
        "turn": turn,
        "request_id": identifier,
        "assistant_text": response["assistant_text"] if response else None,
        "documents": docs,
        "retrieval_origin": origin,
        "trace": trace,
    }
    if fault == "tool_error":
        transport["tool_fault_observed"] = bool(
            trace
            and trace["complete"]
            and any(tool["status"] == "error" for tool in trace["tools"])
        )
    event = recorder.emit(
        "state.changed",
        summary={"kind": "external_http_evidence", **transport, **round_view},
    )
    round_view["event_id"] = transport["event_id"] = event
    return {"round": round_view, "transport": transport, "response": response}


def capture_fact(port, binding, identifier, deadline, recorder, phase):
    fact = port.readback(identifier, binding["session_id"], deadline=deadline)
    if fact["status"] != "not_applicable":
        fact["event_id"] = recorder.emit(
            "state.changed",
            summary={"kind": "external_readback", "phase": phase, **fact},
        )
    return fact
