"""Review regressions: later tool context and paused vector attempts."""

import json
import time
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, messages_to_dict
from test_copilot import ask, context, create
from test_copilot import lab as base_lab
from test_copilot_rag_integration import ControlledRag

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.copilot.embedding import EmbeddingClient
from app.copilot.service import CopilotService
from app.copilot.sources import SourceTools

lab = base_lab


@pytest.mark.parametrize("invalid", [False, True])
def test_short_citations_map_only_to_explicit_current_sources(lab, invalid):
    class Gateway:
        def invoke(self, messages, tools, **kwargs):
            source = json.loads(messages[-1].content)["source"][0]
            answer = {
                "facts": [
                    {
                        "text": "机械引用控制。",
                        "citations": ["C999" if invalid else source["citation_key"]],
                    }
                ],
                "possible_causes": [],
                "missing_evidence": [],
                "next_steps": [],
                "action_ids": [],
                "certainty": "limited",
            }
            return AIMessage(json.dumps(answer)), {"call_id": str(uuid4()), "status": "completed"}

    service = CopilotService(
        lab["engine"],
        lab["redis"],
        lab["settings"],
        gateway_factory=lambda _: Gateway(),
        rag_factory=ControlledRag,
    )
    result = ask(service, create(lab, service=service), "机械短标识控制")
    if invalid:
        assert result["status"] == "failed" and result["error_code"] == "copilot_reference_invalid"
    else:
        assert result["status"] == "completed"
        assert result["answer"]["facts"][0]["citations"][0] in {
            s["source_id"] for s in result["sources"]
        }


def test_unknown_vector_attempt_pauses_new_identifiers(tmp_path):
    calls = []

    def timeout(*args):
        calls.append(1)
        raise TimeoutError()

    client = EmbeddingClient("unused", tmp_path, transport=timeout)
    with pytest.raises(AgentError) as unknown:
        client.embed(["机械问题"], "query", uuid4(), time.monotonic() + 10)
    assert unknown.value.embedding_record["status"] == "unknown"
    with pytest.raises(AgentError) as paused:
        client.embed(["另一问题"], "query", uuid4(), time.monotonic() + 10)
    assert paused.value.embedding_record["status"] == "paused"
    assert len(calls) == 1


def test_later_tool_evidence_displaces_optional_knowledge(lab):
    class EvidenceTools(SourceTools):
        count = 0

        def read_guide(self):
            self.count += 1
            length = 3500 if self.count == 1 else 8000
            return self.add("read_guide", "机械指南", {"body": "g" * length}, limit=9000)

        def read_environment(self):
            return self.add("read_environment", "机械环境证据", {"body": "e" * 8000}, limit=9000)

    class OptionalRag(ControlledRag):
        def retrieve(self, *args):
            _, status, guidance, receipt = super().retrieve(*args)
            chunks = [
                {"kind": "knowledge", "title": str(i), "text": "说明" * 500} for i in range(3)
            ]
            return chunks, status, guidance, receipt

    class TwoRoundGateway:
        calls = 0

        def invoke(self, messages, tools, **kwargs):
            self.calls += 1
            envelope = {
                "messages": messages_to_dict(messages),
                "tools": tools,
                "response_format": {"type": "json_object"},
            }
            assert len(json.dumps(envelope, ensure_ascii=False).encode()) <= 32768
            call = {"call_id": str(uuid4()), "status": "completed"}
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "a", "name": "read_guide", "args": {}},
                        {"id": "b", "name": "read_environment", "args": {}},
                    ],
                ), call
            source = json.loads(messages[-1].content)
            assert len(source["content"]["body"]) == 8000
            answer = {
                "facts": [{"text": "机械当前证据。", "citations": [source["source_id"]]}],
                "possible_causes": [],
                "missing_evidence": [],
                "next_steps": [],
                "action_ids": [],
                "certainty": "limited",
            }
            return AIMessage(json.dumps(answer)), call

    gateway = TwoRoundGateway()
    service = CopilotService(
        lab["engine"],
        lab["redis"],
        lab["settings"],
        tools_factory=lambda owner, value: EvidenceTools(
            lab["engine"], lab["settings"], owner, value
        ),
        gateway_factory=lambda _: gateway,
        rag_factory=OptionalRag,
    )
    created = create(lab, context("environment"), service=service)
    result = ask(service, created, "机械后续工具预算控制")
    assert result["status"] == "completed", result
    assert gateway.calls == 2 and result["knowledge_context_truncated"] is True
    assert all(s["content_hash"] == canonical_hash(s["content"]) for s in result["sources"])
