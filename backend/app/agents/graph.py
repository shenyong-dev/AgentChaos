"""One LangGraph loop. Wait, authorization and side effects are separate nodes."""

import copy
import json
import time
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.context import compose_context
from app.agents.errors import AgentError
from app.agents.state import AgentState
from app.evidence.recorder import semantic


def looks_like_decline(text: str):
    return any(
        word in text
        for word in ("不确认", "先不预约", "不预约了", "不要提交", "拒绝确认", "取消吧", "暂不预约")
    )


def build_agent_graph(gateway, registry, checkpointer, *, control=None, recorder=None):
    prompt = Path(__file__).with_name("prompt.txt").read_text(encoding="utf-8")
    schema_bytes = len(json.dumps(registry.schemas, ensure_ascii=False).encode())

    def intake(state: AgentState):
        return {"model_calls_this_turn": 0, "write_attempted_this_turn": False, "last_error": None}

    def model(state: AgentState, config: RunnableConfig):
        if control:
            control.step("model")
        if state.get("model_calls_this_turn", 0) >= 12:
            raise AgentError("agent_step_limit", "本轮模型调用达到12步上限。", 429)
        context, summary = compose_context(
            state,
            prompt,
            tool_schema_bytes=schema_bytes,
            output_reserve=getattr(
                getattr(gateway, "settings", None), "agent_max_output_tokens", 1024
            ),
        )
        started = None
        if recorder:
            started = recorder.emit(
                "model.started",
                node="model",
                parent=config["configurable"].get("trace_node_parent"),
                visit=config["configurable"].get("trace_node_visit"),
                **recorder.model_metadata,
            )
        try:
            message, record = gateway.invoke(
                context, registry.schemas, deadline=config["configurable"].get("deadline")
            )
        except Exception as exc:
            if recorder:
                recorder.model_end(
                    started,
                    getattr(exc, "agent_record", {"model_requests": 0}),
                    visit=config["configurable"].get("trace_node_visit"),
                    error=getattr(exc, "code", "model_adapter_error"),
                )
            raise
        if recorder:
            recorder.model_end(
                started, record, visit=config["configurable"].get("trace_node_visit")
            )
        if control:
            control.check()
        audit = state.get("audit", []) + [
            {
                "type": "model",
                "call_id": record["call_id"],
                "source_turn": state["turn_index"],
                "usage": record.get("usage"),
                "estimated_peak_microusd": record.get("estimated_peak_microusd"),
                "model_returned": record.get("response", {}).get("model_returned"),
                "streamed": bool(record.get("chunks")),
                "model_requests": record.get("model_requests", 0),
            }
        ]
        dropped = summary["dropped_from_model_context"] > 0
        if dropped:
            audit.append(
                {
                    "type": "context_compacted",
                    "source_turn": state["turn_index"],
                    "summary": summary,
                }
            )
            if recorder:
                recorder.emit(
                    "context.compacted",
                    source_turn=state["turn_index"],
                    summary=summary,
                    parent=started,
                    node="model",
                )
        return {
            "messages": [message],
            "audit": audit,
            "model_calls_this_turn": state.get("model_calls_this_turn", 0) + 1,
            "compactions": state.get("compactions", 0) + int(dropped),
        }

    def tools(state: AgentState, config: RunnableConfig):
        current = copy.deepcopy(state)
        message = current["messages"][-1]
        results = []
        for call in message.tool_calls:
            if control:
                control.step("tool:" + call["name"])
            requested, before = None, None
            if recorder:
                before = recorder.business_snapshot()
                requested = recorder.emit(
                    "tool.requested",
                    tool=call["name"],
                    arguments=call["args"],
                    tool_call_id=call["id"],
                    node="tools",
                    visit=config["configurable"].get("trace_node_visit"),
                    parent=config["configurable"].get("trace_node_parent"),
                )
            capture = {}
            began = time.monotonic()
            try:
                result = registry.dispatch(
                    current,
                    call["name"],
                    call["args"],
                    capture=capture,
                    tool_call_id=call["id"],
                    message_id=message.id,
                    parent=requested,
                )
            except Exception as exc:
                if recorder:
                    recorder.emit(
                        "tool.error",
                        tool=call["name"],
                        arguments=call["args"],
                        raw_result={"exception_type": type(exc).__name__},
                        code="tool_runtime_error",
                        tool_call_id=call["id"],
                        node="tools",
                        parent=requested,
                        elapsed_ms=round((time.monotonic() - began) * 1000, 2),
                        before=before,
                        after=recorder.business_snapshot(),
                    )
                raise
            if recorder:
                fields = result if isinstance(result, dict) else {}
                failed = not isinstance(result, dict) or fields.get("status") == "error"
                recorder.emit(
                    "tool.error" if failed else "tool.completed",
                    tool=call["name"],
                    arguments=call["args"],
                    raw_result=capture.get("raw_result", result),
                    visible_result=result,
                    code=fields.get("code") if isinstance(result, dict) else "tool_format_invalid",
                    commit_state=fields.get("commit_state"),
                    dispatched=capture.get("dispatched"),
                    fault_event_id=capture.get("fault_event_id"),
                    receipt_reused=capture.get("receipt_reused", False),
                    before=before,
                    after=recorder.business_snapshot(),
                    tool_call_id=call["id"],
                    node="tools",
                    parent=requested,
                    visit=config["configurable"].get("trace_node_visit"),
                    elapsed_ms=round((time.monotonic() - began) * 1000, 2),
                )
                if call["name"] == "retrieve_rules":
                    recorder.emit(
                        "retrieval.error" if failed else "retrieval.completed",
                        query=call["args"].get("query", ""),
                        result=result,
                        tool_call_id=call["id"],
                        parent=requested,
                        node="tools",
                    )
            if control:
                control.check()
            results.append(
                ToolMessage(
                    content=result
                    if isinstance(result, str)
                    else json.dumps(result, ensure_ascii=False, default=str),
                    tool_call_id=call["id"],
                )
            )
        for call in message.invalid_tool_calls:
            if recorder:
                requested = recorder.emit(
                    "tool.requested",
                    tool=call.get("name") or "unknown",
                    arguments=call.get("args"),
                    tool_call_id=call.get("id"),
                    node="tools",
                    parent=config["configurable"].get("trace_node_parent"),
                )
                recorder.emit(
                    "tool.error",
                    tool=call.get("name") or "unknown",
                    arguments=call.get("args"),
                    code="invalid_tool_json",
                    raw_result={"code": "invalid_tool_json"},
                    visible_result={"code": "invalid_tool_json"},
                    tool_call_id=call.get("id"),
                    node="tools",
                    parent=requested,
                )
            results.append(
                ToolMessage(
                    content=json.dumps(
                        {
                            "status": "error",
                            "code": "invalid_tool_json",
                            "message": "工具参数不是合法JSON，请重新形成结构化调用。",
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=call["id"],
                )
            )
        current["messages"] = results
        return current

    def after_model(state: AgentState):
        message = state["messages"][-1]
        if isinstance(message, AIMessage) and (message.tool_calls or message.invalid_tool_calls):
            return "tools"
        if state.get("pending_draft") and state.get("confirmation_state") in (
            "pending",
            "authorized",
        ):
            return "wait_for_confirmation"
        return END

    def wait_for_confirmation(state: AgentState):
        decision = interrupt(
            {
                "kind": "booking_confirmation",
                "draft": state["pending_draft"],
                "confirmation_revision": state["confirmation_revision"],
                "unknown_submission": state.get("unknown_submission"),
            }
        )
        return {"resume_action": decision}

    def resolve_confirmation(state: AgentState):
        decision = state["resume_action"]
        current = copy.deepcopy(state)
        current["turn_index"] += 1
        text = decision["text"]
        current["last_user_text"] = text
        current["resume_action"] = None
        current["messages"] = [HumanMessage(content=text, id=decision["request_id"])]
        if decision["kind"] == "confirm":
            if (
                decision["draft_id"] != state["pending_draft"]["draft_id"]
                or decision["draft_hash"] != state["pending_draft"]["draft_hash"]
                or decision["revision"] != state["confirmation_revision"]
            ):
                raise AgentError("confirmation_stale", "恢复绑定与当前确认草稿不一致。")
            current.update(
                {
                    "confirmation_state": "authorized",
                    "grant": {
                        "draft_id": decision["draft_id"],
                        "draft_hash": decision["draft_hash"],
                        "revision": decision["revision"],
                        "user_request_id": decision["request_id"],
                    },
                }
            )
        else:
            rejected = decision["kind"] == "reject" or looks_like_decline(text)
            registry.booking.revoke_pending_drafts(registry.context(state), rejected=rejected)
            if rejected:
                current["denials"] = state.get("denials", []) + [
                    {
                        "source_turn": current["turn_index"],
                        "draft_id": state["pending_draft"]["draft_id"],
                        "text": text,
                    }
                ]
            current.update(
                {
                    "confirmation_state": "rejected" if rejected else "idle",
                    "grant": None,
                    "pending_draft": None,
                    "confirmation_revision": state["confirmation_revision"] + 1,
                    "candidate_rooms": [],
                }
            )
        current["audit"].append(
            {
                "type": "confirmation_decision",
                "kind": decision["kind"],
                "source_turn": current["turn_index"],
                "revision": current["confirmation_revision"],
            }
        )
        return current

    builder = StateGraph(AgentState)

    def observed(name, node):
        if not recorder:
            return node

        def wrapped(state: AgentState, config: RunnableConfig):
            parent, visit = recorder.node_start(name)
            before = semantic(state)
            began = time.monotonic()
            local_config = {
                **config,
                "configurable": {
                    **config.get("configurable", {}),
                    "trace_node_parent": parent,
                    "trace_node_visit": visit,
                },
            }
            try:
                result = node(state, local_config) if name in ("model", "tools") else node(state)
            except GraphInterrupt:
                recorder.emit(
                    "confirmation.requested",
                    node=name,
                    parent=parent,
                    draft=state.get("pending_draft"),
                    revision=state["confirmation_revision"],
                )
                recorder.emit(
                    "node.completed",
                    node=name,
                    parent=parent,
                    visit=visit,
                    status="waiting",
                    elapsed_ms=round((time.monotonic() - began) * 1000, 2),
                )
                raise
            except Exception as exc:
                recorder.emit(
                    "node.error",
                    node=name,
                    parent=parent,
                    visit=visit,
                    status="error",
                    code=getattr(exc, "code", type(exc).__name__),
                    elapsed_ms=round((time.monotonic() - began) * 1000, 2),
                )
                raise
            after = semantic({**state, **result})
            if before != after:
                recorder.emit(
                    "state.changed",
                    node=name,
                    parent=parent,
                    source_turn=after.get("turn_index"),
                    before=before,
                    after=after,
                )
            if name == "resolve_confirmation":
                decision = state["resume_action"]
                recorder.emit(
                    "confirmation.resumed",
                    node=name,
                    parent=parent,
                    draft=state.get("pending_draft"),
                    revision=state["confirmation_revision"],
                    decision=decision["kind"],
                    request_id=decision["request_id"],
                )
            recorder.emit(
                "node.completed",
                node=name,
                parent=parent,
                visit=visit,
                status="completed",
                elapsed_ms=round((time.monotonic() - began) * 1000, 2),
            )
            return result

        return wrapped

    for name, node in (
        ("intake", intake),
        ("model", model),
        ("tools", tools),
        ("wait_for_confirmation", wait_for_confirmation),
        ("resolve_confirmation", resolve_confirmation),
    ):
        builder.add_node(name, observed(name, node))
    builder.add_edge(START, "intake")
    builder.add_edge("intake", "model")
    builder.add_conditional_edges("model", after_model, ["tools", "wait_for_confirmation", END])
    builder.add_edge("tools", "model")
    builder.add_edge("wait_for_confirmation", "resolve_confirmation")
    builder.add_edge("resolve_confirmation", "intake")
    return builder.compile(checkpointer=checkpointer)
