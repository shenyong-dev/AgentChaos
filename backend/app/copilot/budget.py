"""Independent T17E cap using the existing durable reservation/settlement protocol."""

from app.agents.errors import AgentError
from app.core.config import PROJECT_ROOT
from app.interactive.budget import InteractionBudget

AUTHORIZATION = "T17E-20261104-copilot-1"
CALL_LIMIT = 60
SESSION_CALL_LIMIT = 12


def authorized(settings):
    return (
        settings.copilot_allow_paid_model_calls
        and settings.copilot_budget_authorization_id == AUTHORIZATION
        and 0 < settings.copilot_budget_usd <= 1
        and settings.copilot_model == "deepseek-flash"
        and settings.copilot_thinking == "disabled"
        and settings.model_timeout_seconds <= 45
        and settings.copilot_max_output_tokens <= 1536
    )


class CopilotBudget(InteractionBudget):
    def __init__(self, redis, settings, session_id, *, directory=None):
        self.redis, self.settings = redis, settings
        self.key = f"ac:t17e:budget:{settings.copilot_budget_authorization_id}:copilot"
        self.session_key = self.key + ":session:" + str(session_id)
        self.calls_key = self.session_key + ":calls"
        self.directory = directory or PROJECT_ROOT / ".local/t17e/budget-tickets" / str(session_id)

    def reserve(self, prompt_bytes, max_output):
        from uuid import uuid4

        from app.agents.budget import peak_estimate_microusd
        from app.executors.budget import RESERVE

        if not authorized(self.settings):
            raise AgentError("copilot_not_authorized", "助手调用开关或独立预算尚未启用。", 403)
        if not 0 <= prompt_bytes <= 32768 or not 1 <= max_output <= 1536:
            raise AgentError("copilot_input_limit", "助手请求超过上下文或输出上限。", 400)
        tokens = prompt_bytes * 2 + 8192
        ticket = {
            "call_id": str(uuid4()),
            "input_token_upper": tokens,
            "max_output_tokens": max_output,
            "upper_microusd": peak_estimate_microusd(tokens, max_output),
        }
        ticket["key"] = self.calls_key + ":" + ticket["call_id"]
        result = self.redis.eval(
            RESERVE,
            4,
            *self.keys(ticket),
            ticket["upper_microusd"],
            tokens + max_output,
            int(self.settings.copilot_budget_usd * 1_000_000),
            100000,
            SESSION_CALL_LIMIT,
            1_000_000,
            CALL_LIMIT,
        )
        if result != 1:
            raise AgentError(
                "copilot_budget_unavailable", "助手账本缺失或金额/请求额度已到，未调用模型。", 429
            )
        try:
            self.record(ticket, "reserved")
        except OSError:
            self.unknown(ticket)
            raise AgentError(
                "copilot_ticket_unavailable", "调用票据无法保存，未发送请求，预留保留。", 503
            ) from None
        return ticket
