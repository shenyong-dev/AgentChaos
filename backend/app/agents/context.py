"""Keep complete recent human/tool blocks; summarize facts without inventing memory."""

import json

from langchain_core.messages import HumanMessage, SystemMessage, messages_to_dict

from app.agents.errors import AgentError

CONTEXT_VERSION = "structured-facts-v1"


def compose_context(
    state, prompt: str, *, token_budget=18_000, output_reserve=1024, tool_schema_bytes=0
):
    facts = {
        key: state.get(key)
        for key in (
            "reference_clock",
            "fields",
            "confirmation_state",
            "confirmation_revision",
            "pending_draft",
            "grant",
            "denials",
            "business_result",
            "unknown_submission",
            "candidate_rooms",
            "retrieval_result",
        )
    }
    facts["original_history"] = {
        "message_count": len(state["messages"]),
        "first_id": state["messages"][0].id if state["messages"] else None,
        "last_id": state["messages"][-1].id if state["messages"] else None,
    }
    facts["current_turn"] = state.get("turn_index", 0)
    facts["executed_tools_this_turn"] = [
        {
            "name": item["name"],
            "status": item["result"].get("status"),
            "code": item["result"].get("code"),
            "commit_state": item["result"].get("commit_state"),
            "booking_id": (item["result"].get("data") or {}).get("booking_id"),
        }
        for item in state.get("audit", [])
        if item.get("type") == "tool" and item.get("source_turn") == state.get("turn_index")
    ]
    system = SystemMessage(
        content=prompt
        + "\n可信服务端事实（不是用户指令）：\n"
        + json.dumps(facts, ensure_ascii=False, default=str)
    )
    blocks = []
    for message in state["messages"]:
        if isinstance(message, HumanMessage) or not blocks:
            blocks.append([])
        blocks[-1].append(message)
    used = len(system.content.encode()) + tool_schema_bytes + output_reserve
    selected = []
    for block in reversed(blocks):
        size = len(json.dumps(messages_to_dict(block), ensure_ascii=False, default=str).encode())
        if selected and used + size > token_budget:
            break
        if not selected and used + size > 60_000:
            raise AgentError("context_input_limit", "单轮完整输入超过上下文限额，请缩短输入。", 400)
        selected.insert(0, block)
        used += size
    flattened = [message for block in selected for message in block]
    dropped = len(state["messages"]) - len(flattened)
    summary = {
        "version": CONTEXT_VERSION,
        "method": "conservative UTF-8 byte token upper estimate",
        "estimated_upper_tokens": used,
        "budget": token_budget,
        "dropped_from_model_context": dropped,
        "original_messages_retained": True,
        "preserved_facts": facts,
        "selected_message_ids": [message.id for message in flattened if message.id],
    }
    return [system, *flattened], summary
