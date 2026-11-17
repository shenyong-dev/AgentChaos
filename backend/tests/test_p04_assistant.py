"""Current case/source integration with a synthetic gateway and isolated Redis keys."""

import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from redis import Redis

from app.agents.errors import AgentError
from app.core.config import Settings
from app.db.engine import create_database_engine
from app.guidance.service import catalog
from app.schemas.copilot import CopilotAsk, CopilotCreate
from benchmarks.p04_trial.assistant import Assistant, PaidService, PilotSources


def test_current_approved_case_and_friendly_action_survive_readback_without_session_recreation():
    settings = Settings().model_copy(update={"copilot_local_unlimited_calls": True})
    engine = create_database_engine(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    prefix = "ac:p04-control:" + uuid4().hex

    class Gateway:
        def invoke(self, messages, tools, **kwargs):
            payload = json.loads(messages[-1].content)
            source = next(row for row in payload["source"] if row["tool"] == "read_selection")
            assert source["content"]["turns_complete"] is True
            assert len(source["content"]["turns"]) == 3
            assert payload["allowed_action_ids"] == ["case.read"]
            answer = {
                "facts": [
                    {
                        "text": "Explicit mechanical source integration",
                        "citations": [source["source_id"]],
                    }
                ],
                "possible_causes": [],
                "missing_evidence": [],
                "next_steps": [{"text": "case.read", "citations": [source["source_id"]]}],
                "action_ids": ["case.read"],
                "certainty": "limited",
            }
            return AIMessage(content=json.dumps(answer)), {
                "call_id": "mechanical-only",
                "role": "copilot",
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

    service = PaidService(
        engine,
        redis,
        settings,
        tools_factory=lambda owner, context: PilotSources(engine, settings, owner, context),
        gateway_factory=lambda _: Gateway(),
    )
    service.prefix = prefix
    assistant = object.__new__(Assistant)
    assistant.service, assistant.binding, assistant.creation_id = (
        service,
        "explicit-control",
        uuid4(),
    )
    assistant.check = lambda: None
    try:
        created = service.create(
            1,
            assistant.binding,
            CopilotCreate(
                request_id=assistant.creation_id,
                page="library",
                catalog_hash=catalog()[1],
                object_kind="case",
                object_id="7",
            ),
        )
        rid = uuid4()
        result = service.ask(
            1,
            assistant.binding,
            created["session_id"],
            CopilotAsk(
                request_id=rid,
                context_hash=created["context_hash"],
                question="Explicit mechanical question",
            ),
        )
        assert result["status"] == "completed"
        assert result["answer"]["next_steps"][0]["text"] == "查看所选用例说明"
        assert assistant.receipt(str(rid))["request_id"] == str(rid)
        redis.delete(service.key(created["session_id"]))
        with pytest.raises(AgentError, match="过期"):
            assistant.receipt(str(rid))
        assert not redis.exists(service.key(created["session_id"]))
    finally:
        keys = list(redis.scan_iter(match=prefix + "*"))
        assert all(key.startswith(prefix) for key in keys)
        if keys:
            redis.delete(*keys)
        engine.dispose()
