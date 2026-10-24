"""Offline compatibility probe. No HTTP/model requests and no persistence writes."""

import json
from importlib.metadata import version
from typing import TypedDict

from langchain_deepseek import ChatDeepSeek
from langgraph.checkpoint.redis import RedisSaver
from langgraph.graph import END, START, StateGraph

from app.core.config import get_settings
from app.workers.celery_app import celery_app


class ProbeState(TypedDict):
    value: int


graph = StateGraph(ProbeState)
graph.add_node("increment", lambda state: {"value": state["value"] + 1})
graph.add_edge(START, "increment")
graph.add_edge("increment", END)
result = graph.compile().invoke({"value": 1})
assert result == {"value": 2}

settings = get_settings()
model = ChatDeepSeek(
    model=settings.agent_model,
    api_key="offline-placeholder-not-a-real-key",
    api_base=settings.deepseek_base_url,
    extra_body={"thinking": {"type": settings.agent_thinking}},
    timeout=settings.model_timeout_seconds,
    max_retries=0,
    max_tokens=settings.agent_max_output_tokens,
)
model.bind_tools(
    [
        {
            "type": "function",
            "function": {
                "name": "offline_probe",
                "description": "Offline schema construction only",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
    ]
)
assert RedisSaver is not None
assert celery_app.conf.accept_content == ["json"]
print(
    json.dumps(
        {
            "probe": "offline_only",
            "langgraph_node_execution": "passed",
            "deepseek_client_and_tool_binding": "passed",
            "model_requests": 0,
            "redis_checkpointer_import": "passed",
            "celery_config_import": "passed",
            "versions": {
                name: version(name)
                for name in (
                    "fastapi",
                    "pydantic",
                    "sqlalchemy",
                    "pymysql",
                    "redis",
                    "celery",
                    "langgraph",
                    "langchain-deepseek",
                    "langgraph-checkpoint-redis",
                )
            },
        },
        ensure_ascii=False,
        indent=2,
    )
)
