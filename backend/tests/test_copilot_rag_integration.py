"""Real isolated SQL/Redis, controlled vector and generation gateways; no provider calls."""

import json
from uuid import uuid4

from langchain_core.messages import AIMessage
from test_copilot import ask, context, create
from test_copilot import lab as base_lab

from app.agents.versions import canonical_hash
from app.copilot.service import CopilotService
from app.copilot.sources import SourceTools

lab = base_lab


class ControlledRag:
    def retrieve(self, question, tools, identifier, deadline):
        assert tools.observation["role"] == "operator"
        return (
            [{"kind": "knowledge", "title": "机械知识", "text": "仅用于接线控制，不是实际回答。"}],
            {"kind": "retrieval_status", "status": "matched", "matches": 1},
            {
                "scenario": "getting_started",
                "title": "开始使用",
                "ambiguous": False,
                "followups": ["机械追问，仅填草稿"],
            },
            {"status": "completed", "usage": {"total_tokens": 3}, "request_id": "synthetic"},
        )


class ReplyGateway:
    def __init__(self):
        self.payload = None

    def invoke(self, messages, tools, **kwargs):
        self.payload = json.loads(messages[-1].content)
        source = next(s for s in self.payload["source"] if s["tool"] == "read_guide")
        return AIMessage(
            json.dumps(
                {
                    "facts": [{"text": "明确机械接线结果。", "citations": [source["source_id"]]}],
                    "possible_causes": [],
                    "missing_evidence": [],
                    "next_steps": [],
                    "action_ids": [],
                    "certainty": "limited",
                },
                ensure_ascii=False,
            )
        ), {"call_id": str(uuid4()), "status": "completed"}


def test_rag_is_preloaded_into_actual_copilot_loop_and_receipt(lab):
    gateway = ReplyGateway()
    service = CopilotService(
        lab["engine"],
        lab["redis"],
        lab["settings"],
        gateway_factory=lambda _: gateway,
        rag_factory=ControlledRag,
    )
    created = create(lab, service=service)
    result = ask(service, created, "机械新问题，不是真人试用")
    assert result["status"] == "completed"
    assert result["guidance"]["scenario"] == "getting_started"
    assert result["embedding"]["usage"]["total_tokens"] == 3
    assert any(s["content"].get("kind") == "knowledge" for s in gateway.payload["source"])
    assert gateway.payload["guidance"]["title"] == "开始使用"
    assert all(s["content_hash"] == canonical_hash(s["content"]) for s in result["sources"])


def test_protected_page_never_constructs_rag_or_model(lab):
    def forbidden(*args):
        raise AssertionError("Protected page attempted a provider")

    service = CopilotService(
        lab["engine"],
        lab["redis"],
        lab["settings"],
        gateway_factory=forbidden,
        rag_factory=forbidden,
    )
    created = create(lab, context("judging"), service=service)
    result = ask(service, created, "受保护页面控制")
    assert result["status"] == "restricted" and not result["calls"]


def test_missing_index_keeps_readable_status_without_fabricating_knowledge(lab):
    def broken():
        raise ValueError("synthetic broken index")

    gateway = ReplyGateway()
    service = CopilotService(
        lab["engine"],
        lab["redis"],
        lab["settings"],
        gateway_factory=lambda _: gateway,
        rag_factory=broken,
    )
    result = ask(service, create(lab, service=service), "机械索引失败控制")
    assert result["status"] == "completed"
    assert any(s["content"].get("status") == "embedding_unavailable" for s in result["sources"])
    assert not any(s["content"].get("kind") == "knowledge" for s in result["sources"])


def test_context_budget_drops_optional_knowledge_but_keeps_original_source_hash(lab):
    class LargeTools(SourceTools):
        def read_guide(self):
            return self.add("read_guide", "机械大指南", {"body": "x" * 20000}, limit=22000)

    class LargeRag(ControlledRag):
        def retrieve(self, *args):
            chunks, status, guidance, receipt = super().retrieve(*args)
            chunks = [
                {"kind": "knowledge", "title": str(i), "text": "说明" * 500} for i in range(3)
            ]
            return chunks, status, guidance, receipt

    gateway = ReplyGateway()
    service = CopilotService(
        lab["engine"],
        lab["redis"],
        lab["settings"],
        tools_factory=lambda owner, value: LargeTools(lab["engine"], lab["settings"], owner, value),
        gateway_factory=lambda _: gateway,
        rag_factory=LargeRag,
    )
    result = ask(service, create(lab, service=service), "机械字节预算控制")
    assert result["status"] == "completed", result
    assert result["knowledge_context_truncated"] is True
    assert (
        len(next(s for s in result["sources"] if s["tool"] == "read_guide")["content"]["body"])
        == 20000
    )
    assert all(s["content_hash"] == canonical_hash(s["content"]) for s in result["sources"])
