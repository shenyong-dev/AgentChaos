"""Two real, budgeted agent requests: streamed tool call and correctly paired result."""

import json
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from redis import Redis

from app.agents.budget import PRICING, AgentBudget
from app.agents.gateway import ModelGateway
from app.core.config import PROJECT_ROOT, get_settings


def main():
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, socket_timeout=10, socket_connect_timeout=5)
    budget = AgentBudget(redis, settings)
    gateway = ModelGateway(settings, budget)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "echo_probe",
                "description": "连通性探测，原样返回text，不修改业务数据。",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    messages = [
        SystemMessage(content="你是接口协议探测助手。按要求调用工具，不执行预约。"),
        HumanMessage(content="请调用echo_probe，text填写中文连通；得到回传后仅回复测试完成。"),
    ]
    first, first_record = gateway.invoke(messages, tools)
    assert len(first.tool_calls) == 1 and first.tool_calls[0]["name"] == "echo_probe"
    call = first.tool_calls[0]
    assert call["args"] == {"text": "中文连通"}
    messages.extend(
        [
            first,
            ToolMessage(
                content=json.dumps({"echo": call["args"]["text"]}, ensure_ascii=False),
                tool_call_id=call["id"],
            ),
        ]
    )
    second, second_record = gateway.invoke(messages, tools)
    assert not second.tool_calls and "测试完成" in second.content
    records = [first_record, second_record]
    assert all(record["usage"] for record in records)
    assert all(
        "".join(chunk["content"] for chunk in record["chunks"] if isinstance(chunk["content"], str))
        == record["response"]["content"]
        for record in records
    )
    evidence = {
        "checked_at": datetime.now(UTC).isoformat(),
        "scope": "T04 agent only, non-thinking; no booking writes",
        "pricing": PRICING,
        "tool_arguments": "passed",
        "tool_call_id_roundtrip": "passed",
        "stream_content_matches_final": "passed",
        "usage_returned": "passed",
        "thinking_mode": "not tested",
        "records": records,
        "budget_after": budget.snapshot(),
    }
    root = PROJECT_ROOT / "docs/evidence/T04"
    root.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
    secret = settings.deepseek_api_key.get_secret_value()
    assert secret not in serialized
    (root / "model-probe.json").write_text(serialized + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "probe": "passed",
                "real_model_requests": 2,
                "returned_model": second_record["response"]["model_returned"],
                "budget": budget.snapshot(),
            },
            ensure_ascii=False,
        )
    )
    redis.close()


if __name__ == "__main__":
    main()
