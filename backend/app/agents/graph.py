"""One LangGraph loop. Wait, authorization and side effects are separate nodes."""

import copy
import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.context import compose_context
from app.agents.errors import AgentError
from app.agents.state import AgentState


def looks_like_decline(text: str):
    return any(
        word in text
        for word in ("不确认", "先不预约", "不预约了", "不要提交", "拒绝确认", "取消吧", "暂不预约")
    )


def build_agent_graph(gateway, registry, checkpointer):
    prompt = Path(__file__).with_name("prompt.txt").read_text(encoding="utf-8")
    schema_bytes = len(json.dumps(registry.schemas, ensure_ascii=False).encode())

    def intake(state: AgentState):
        return {"model_calls_this_turn": 0, "write_attempted_this_turn": False, "last_error": None}

    def model(state: AgentState, config: RunnableConfig):
        if state.get("model_calls_this_turn", 0) >= 12:
            raise AgentError("agent_step_limit", "本轮模型调用达到12步上限。", 429)
        context, summary = compose_context(state, prompt, tool_schema_bytes=schema_bytes)
        message, record = gateway.invoke(
            context, registry.schemas, deadline=config["configurable"].get("deadline")
        )
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
        return {
            "messages": [message],
            "audit": audit,
            "model_calls_this_turn": state.get("model_calls_this_turn", 0) + 1,
            "compactions": state.get("compactions", 0) + int(dropped),
        }

    def tools(state: AgentState):
        current = copy.deepcopy(state)
        message = current["messages"][-1]
        results = []
        for call in message.tool_calls:
            result = registry.dispatch(current, call["name"], call["args"])
            results.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False, default=str),
                    tool_call_id=call["id"],
                )
            )
        for call in message.invalid_tool_calls:
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
    for name, node in (
        ("intake", intake),
        ("model", model),
        ("tools", tools),
        ("wait_for_confirmation", wait_for_confirmation),
        ("resolve_confirmation", resolve_confirmation),
    ):
        builder.add_node(name, node)
    builder.add_edge(START, "intake")
    builder.add_edge("intake", "model")
    builder.add_conditional_edges("model", after_model, ["tools", "wait_for_confirmation", END])
    builder.add_edge("tools", "model")
    builder.add_edge("wait_for_confirmation", "resolve_confirmation")
    builder.add_edge("resolve_confirmation", "intake")
    return builder.compile(checkpointer=checkpointer)
