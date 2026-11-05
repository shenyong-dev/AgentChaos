"""T17D finite approval with atomic global/session/call accounting."""

import json
from pathlib import Path
from uuid import uuid4

from app.agents.budget import peak_estimate_microusd
from app.agents.errors import AgentError
from app.core.config import PROJECT_ROOT
from app.executors.budget import RESERVE, SETTLE, UNKNOWN

AUTHORIZATION = "T17D-20261102-agent-1"
GLOBAL_LIMIT = 1_000_000
CALL_LIMIT = 120
SESSION_LIMIT = 100_000
SESSION_CALL_LIMIT = 20
LOCAL_AUTHORIZATION = "LOCAL-20261105-interactive-open"

# Local opt-in has no cumulative cap. Individual requests retain the original
# input/output bounds, runtime deadline, durable reservation and usage checks.
LOCAL_RESERVE = """
if tonumber(redis.call('HGET',KEYS[1],'limit') or '0')~=-1 or
   redis.call('HGET',KEYS[1],'protocol_overrun') then return -1 end
for i=1,3 do
 for _,field in ipairs({'charged_estimate','held_upper','calls_started','token_upper'}) do
  if tonumber(redis.call('HGET',KEYS[i],field) or '0')<0 then return -1 end
 end
end
local upper=tonumber(ARGV[1]); local tokens=tonumber(ARGV[2])
for i=1,3 do
 redis.call('HINCRBY',KEYS[i],'held_upper',upper)
 redis.call('HINCRBY',KEYS[i],'calls_started',1)
 redis.call('HINCRBY',KEYS[i],'token_upper',tokens)
end
redis.call('HSET',KEYS[4],'state','reserved','upper',upper,'tokens',tokens)
return 1
"""


def local_unlimited(settings):
    return (
        settings.app_env == "development"
        and settings.interactive_local_unlimited
        and settings.model_budget_authorization_id == LOCAL_AUTHORIZATION
    )


def limits(settings):
    unlimited = local_unlimited(settings)
    return {
        "session_microusd": None if unlimited else SESSION_LIMIT,
        "session_calls": None if unlimited else SESSION_CALL_LIMIT,
        "total_calls": None if unlimited else CALL_LIMIT,
        "round_seconds": 120,
        "local_unlimited": unlimited,
    }


def authorized(settings):
    return (
        settings.allow_paid_model_calls
        and (
            local_unlimited(settings)
            or (
                not settings.interactive_local_unlimited
                and settings.model_budget_authorization_id == AUTHORIZATION
                and 0 < settings.agent_budget_usd <= 1
            )
        )
        and settings.agent_model == "deepseek-flash"
        and settings.agent_thinking == "disabled"
        and 1 <= settings.agent_max_output_tokens <= 1024
    )


class InteractionBudget:
    def __init__(self, redis, settings, session_id, *, directory: Path | None = None):
        self.redis, self.settings = redis, settings
        namespace = "ac:local:interactive" if local_unlimited(settings) else "ac:t17d"
        self.key = f"{namespace}:budget:{settings.model_budget_authorization_id}:agent"
        self.session_key = self.key + ":session:" + str(session_id)
        self.calls_key = self.session_key + ":calls"
        self.directory = directory or PROJECT_ROOT / ".local/t17d/budget-tickets" / str(session_id)

    def keys(self, ticket):
        return [self.key, self.session_key, self.calls_key, ticket["key"]]

    def record(self, ticket, state, **extra):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (ticket["call_id"] + ".json")
        old = json.loads(path.read_bytes()) if path.exists() else {}
        path.write_text(
            json.dumps({**old, "ticket": ticket, "state": state, **extra}, indent=2) + "\n",
            encoding="utf-8",
        )

    def reserve(self, prompt_bytes, max_output):
        if not authorized(self.settings):
            raise AgentError("interactive_not_authorized", "交互调用授权或模型配置未匹配。", 403)
        if not 0 <= prompt_bytes <= 65_536 or not 1 <= max_output <= 1024:
            raise AgentError("interactive_input_limit", "交互请求超过输入或输出上限。", 400)
        tokens = prompt_bytes * 2 + 8192
        ticket = {
            "call_id": str(uuid4()),
            "input_token_upper": tokens,
            "max_output_tokens": max_output,
            "upper_microusd": peak_estimate_microusd(tokens, max_output),
        }
        ticket["key"] = self.calls_key + ":" + ticket["call_id"]
        result = self.redis.eval(
            LOCAL_RESERVE if local_unlimited(self.settings) else RESERVE,
            4,
            *self.keys(ticket),
            ticket["upper_microusd"],
            tokens + max_output,
            int(self.settings.agent_budget_usd * 1_000_000),
            SESSION_LIMIT,
            SESSION_CALL_LIMIT,
            1_000_000,
            CALL_LIMIT,
        )
        if result != 1:
            raise AgentError(
                "interactive_budget_unavailable", "账本缺失或金额/请求上限已到，未调用模型。", 429
            )
        try:
            self.record(ticket, "reserved")
        except OSError:
            self.unknown(ticket)
            raise AgentError(
                "interactive_ticket_unavailable",
                "用量票据无法保存，未发送模型请求，预留保留。",
                503,
            ) from None
        return ticket

    def settle(self, ticket, usage):
        if not usage or any(
            type(usage.get(k)) is not int for k in ("input_tokens", "output_tokens")
        ):
            self.unknown(ticket)
            return None
        if not (
            0 <= usage["input_tokens"] <= ticket["input_token_upper"]
            and 0 <= usage["output_tokens"] <= ticket["max_output_tokens"]
        ):
            self.redis.hset(self.key, "protocol_overrun", 1)
            self.unknown(ticket)
            raise AgentError("interactive_usage_mismatch", "用量超出预留假设，停止新调用。", 503)
        estimate = peak_estimate_microusd(usage["input_tokens"], usage["output_tokens"])
        result = self.redis.eval(
            SETTLE,
            4,
            *self.keys(ticket),
            estimate,
            usage["input_tokens"] + usage["output_tokens"],
            json.dumps(usage),
        )
        if result < 0:
            raise AgentError("interactive_settlement_failed", "结算失败，未知预留保留。", 503)
        self.record(ticket, "settled", usage=usage, estimate_microusd=estimate)
        return estimate

    def unknown(self, ticket):
        if self.redis.eval(UNKNOWN, 4, *self.keys(ticket)):
            try:
                self.record(ticket, "unknown")
            except OSError:
                pass  # Redis retains the durable reservation even if local disk is unavailable.

    def snapshot(self):
        return {
            (k.decode() if isinstance(k, bytes) else k): int(v)
            for k, v in self.redis.hgetall(self.session_key).items()
        }
