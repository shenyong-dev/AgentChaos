"""Explicit adversarial protocol controls, never natural DeepSeek defect evidence."""

from types import SimpleNamespace

import pytest
from agent_test_support import OfflineGateway
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.graph import build_agent_graph


class Registry:
    schemas = []

    def __init__(self, policy):
        self.settings = SimpleNamespace(agent_tool_policy=policy)
        self.dispatched = []

    def dispatch(self, state, name, args, **kwargs):
        self.dispatched.append((name, args))
        return {"status": "ok"}


def run(policy, *, invalid=False, recover=False):
    registry = Registry(policy)
    gateway = OfflineGateway()
    calls = [{"id": "one", "name": "preview_booking", "args": {}}]
    if not invalid:
        calls.append({"id": "two", "name": "confirm_booking", "args": {}})
    gateway.queue.append(
        AIMessage(
            content="",
            tool_calls=calls,
            invalid_tool_calls=[{"id": "bad", "name": "confirm_booking", "args": "{"}]
            if invalid
            else [],
        )
    )
    if recover:
        gateway.queue.append(
            AIMessage(
                content="", tool_calls=[{"id": "retry", "name": "retrieve_rules", "args": {}}]
            )
        )
    gateway.queue.append(AIMessage(content="Explicit offline control, no quality claim."))
    graph = build_agent_graph(gateway, registry, InMemorySaver())
    result = graph.invoke(
        {"messages": [HumanMessage(content="机械控制")], "turn_index": 1, "audit": []},
        {"configurable": {"thread_id": "policy-control"}},
    )
    return registry, [m for m in result["messages"] if isinstance(m, ToolMessage)]


def test_baseline_dispatches_both_calls_and_candidate_rejects_entire_batch():
    old, _ = run("baseline")
    new, replies = run("single_call_v1")
    assert len(old.dispatched) == 2
    assert new.dispatched == []
    assert [r.tool_call_id for r in replies] == ["one", "two"]
    assert all("single_tool_call_required" in r.content for r in replies)


@pytest.mark.parametrize("invalid", [False, True])
def test_rejected_valid_or_mixed_batch_can_recover_with_single_call(invalid):
    registry, replies = run("single_call_v1", invalid=invalid, recover=True)
    assert registry.dispatched == [("retrieve_rules", {})]
    assert len(replies) == 3 and replies[-1].tool_call_id == "retry"
    assert "single_tool_call_required" in replies[0].content
    if invalid:
        assert replies[1].tool_call_id == "bad" and "invalid_tool_json" in replies[1].content
