"""One DeepSeek streaming boundary with real usage and sanitized observable evidence."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    message_chunk_to_message,
    messages_to_dict,
)
from langchain_deepseek import ChatDeepSeek
from openai import APITimeoutError

from app.agents.budget import PRICING, AgentBudget
from app.agents.errors import AgentError
from app.core.config import PROJECT_ROOT, Settings
from app.core.model_probe import remember_success


class ModelGateway:
    def __init__(
        self, settings: Settings, budget: AgentBudget, *, audit_directory: Path | None = None
    ):
        self.settings, self.budget = settings, budget
        self.audit_directory = audit_directory or PROJECT_ROOT / ".local/t04/model-calls"

    def invoke(
        self,
        messages: list[BaseMessage],
        tools: list[dict],
        *,
        deadline: float | None = None,
        tool_choice=None,
    ) -> tuple[AIMessage, dict]:
        s = self.settings
        if not s.deepseek_api_key.get_secret_value():
            raise AgentError("model_key_missing", "本地模型密钥未配置。", 503)
        if s.agent_thinking != "disabled":
            raise AgentError("model_mode_unverified", "T04基线只开放已验证的非思考模式。", 400)
        remaining = min(
            s.model_timeout_seconds, (deadline or time.monotonic() + 120) - time.monotonic()
        )
        if remaining <= 0:
            raise AgentError("agent_deadline", "本轮对话达到总时限。", 504)
        request = {"messages": messages_to_dict(messages), "tools": tools}
        request_bytes = len(json.dumps(request, ensure_ascii=False, default=str).encode())
        ticket = self.budget.reserve(request_bytes, s.agent_max_output_tokens)
        record = {
            "call_id": ticket["call_id"],
            "role": "agent",
            "model_requested": s.agent_model,
            "started_at": datetime.now(UTC).isoformat(),
            "thinking": "disabled",
            "request": request,
            "reservation_microusd": ticket["upper_microusd"],
            "pricing": PRICING,
            "chunks": [],
            "model_requests": 1,
        }
        started = time.monotonic()
        try:
            model = ChatDeepSeek(
                model=s.agent_model,
                api_key=s.deepseek_api_key.get_secret_value(),
                api_base=s.deepseek_base_url,
                temperature=None,
                extra_body={"thinking": {"type": "disabled"}},
                timeout=remaining,
                max_retries=0,
                max_tokens=s.agent_max_output_tokens,
            )
            bound = (
                model.bind_tools(tools, **({"tool_choice": tool_choice} if tool_choice else {}))
                if tools
                else model
            )
            combined = None
            stream = bound.stream(messages, stream_usage=True)
            try:
                for chunk in stream:
                    if deadline is not None and time.monotonic() > deadline:
                        raise AgentError("agent_deadline", "本轮对话达到总时限。", 504)
                    record["chunks"].append(
                        {
                            "content": chunk.content,
                            "tool_call_chunks": chunk.tool_call_chunks,
                            "usage": chunk.usage_metadata,
                        }
                    )
                    combined = chunk if combined is None else combined + chunk
            finally:
                stream.close()
            if combined is None:
                raise AgentError("model_empty_stream", "模型未返回可观察输出。", 503)
            message = message_chunk_to_message(combined)
            metadata = message.response_metadata
            record.update(
                {
                    "response": {
                        "content": message.content,
                        "tool_calls": message.tool_calls,
                        "invalid_tool_calls": message.invalid_tool_calls,
                        "model_returned": metadata.get("model_name") or metadata.get("model"),
                        "finish_reason": metadata.get("finish_reason"),
                        "id": message.id,
                    },
                    "usage": message.usage_metadata,
                    "provider_token_usage": metadata.get("token_usage"),
                }
            )
            record["estimated_peak_microusd"] = self.budget.settle(ticket, message.usage_metadata)
            if metadata.get("finish_reason") == "length":
                raise AgentError(
                    "model_output_truncated", "模型输出达到上限，不能作为完整结果。", 503
                )
            if not message.content and not message.tool_calls and not message.invalid_tool_calls:
                raise AgentError("model_empty_response", "模型没有返回内容或工具调用。", 503)
            record["status"] = "completed"
            try:
                remember_success(s, record)
            except OSError:
                record["local_observation_write"] = "unavailable"
            return message, record
        except Exception as exc:
            self.budget.unknown(ticket)
            record.update(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "http_status": getattr(exc, "status_code", None),
                }
            )
            if isinstance(exc, AgentError):
                record["error_code"] = exc.code
                raise
            code = "model_service_error"
            status = 503
            if isinstance(exc, (APITimeoutError, TimeoutError)):
                code, status = "model_timeout", 504
            elif getattr(exc, "status_code", None) == 429:
                code = "model_rate_limited"
            record["error_code"] = code
            raise AgentError(code, "DeepSeek请求未完成；未知用量保留预留。", status) from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            self.audit_directory.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(record, ensure_ascii=False, indent=2, default=str)
            secret = s.deepseek_api_key.get_secret_value()
            if secret:
                serialized = serialized.replace(secret, "[REDACTED]")
            (self.audit_directory / f"{ticket['call_id']}.json").write_text(
                serialized + "\n", encoding="utf-8"
            )
