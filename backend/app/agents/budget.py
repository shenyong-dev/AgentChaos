"""T04 approval-scoped agent cap. T05 provides platform run/role accounting."""

import json
import math
from decimal import ROUND_CEILING, Decimal
from uuid import uuid4

from redis import Redis

from app.agents.errors import AgentError
from app.core.config import Settings

PRICING = {
    "model": "deepseek-flash",
    "verified_on": "2026-10-25",
    "documented_version": "DeepSeek-V4.1-Flash-1017",
    "source": "https://api-docs.deepseek.com/quick_start/pricing/",
    "currency": "USD",
    "peak_input_miss_per_million": "0.30",
    "peak_output_per_million": "1.20",
    "method": "peak-rate upper estimate, all input charged at cache-miss rate; not a bill",
}
RESERVE = """
local charged=tonumber(redis.call('HGET',KEYS[1],'charged_estimate') or '0')
local held=tonumber(redis.call('HGET',KEYS[1],'held_upper') or '0')
local calls=tonumber(redis.call('HGET',KEYS[1],'calls_started') or '0')
local limit=tonumber(redis.call('HGET',KEYS[1],'limit') or ARGV[1])
if tonumber(ARGV[1])<limit then limit=tonumber(ARGV[1]) end
if charged+held+tonumber(ARGV[2])>limit or calls>=400 then return 0 end
redis.call('HSET',KEYS[1],'limit',limit)
redis.call('HINCRBY',KEYS[1],'held_upper',ARGV[2])
redis.call('HINCRBY',KEYS[1],'calls_started',1)
redis.call('HSET',KEYS[2],'state','reserved','upper',ARGV[2])
return 1
"""
SETTLE = """
if redis.call('HGET',KEYS[2],'state')~='reserved' then return 0 end
local upper=tonumber(redis.call('HGET',KEYS[2],'upper'))
if tonumber(ARGV[1])>upper then
 redis.call('HSET',KEYS[1],'protocol_overrun',1)
 redis.call('HSET',KEYS[2],'state','unknown')
 return -1
end
redis.call('HINCRBY',KEYS[1],'held_upper',-upper)
redis.call('HINCRBY',KEYS[1],'charged_estimate',ARGV[1])
redis.call('HINCRBY',KEYS[1],'calls_completed',1)
redis.call('HSET',KEYS[2],'state','settled','estimate',ARGV[1])
return 1
"""


def peak_estimate_microusd(input_tokens: int, output_tokens: int) -> int:
    estimate = input_tokens * Decimal("0.30") + output_tokens * Decimal("1.20")
    return int(estimate.to_integral_value(rounding=ROUND_CEILING))


class AgentBudget:
    def __init__(self, redis: Redis, settings: Settings, *, prefix="ac:t04:budget"):
        self.redis, self.settings = redis, settings
        self.key = f"{prefix}:{settings.model_budget_authorization_id}:agent"

    def reserve(self, prompt_bytes: int, max_output: int):
        s = self.settings
        if (
            not s.allow_paid_model_calls
            or s.agent_budget_usd <= 0
            or s.agent_budget_usd > 1
            or s.agent_model != "deepseek-flash"
            or s.model_budget_authorization_id != "T04-20261025-agent-1"
        ):
            raise AgentError("model_not_authorized", "T04模型调用或预算尚未获准。", 403)
        if prompt_bytes > 65_536 or not 1 <= max_output <= 4096:
            raise AgentError("model_input_limit", "请求超过本轮模型输入或输出限制。", 400)
        if self.redis.hget(self.key, "protocol_overrun"):
            raise AgentError("budget_protocol_error", "用量超出预留假设，已停止后续调用。", 503)
        input_upper = prompt_bytes * 2 + 8192
        upper = peak_estimate_microusd(input_upper, max_output)
        limit = math.floor(s.agent_budget_usd * 1_000_000)
        call_id = str(uuid4())
        call_key = f"{self.key}:call:{call_id}"
        if self.redis.eval(RESERVE, 2, self.key, call_key, limit, upper) != 1:
            raise AgentError(
                "model_budget_exhausted", "本轮预算或调用次数上限已到，未发送请求。", 429
            )
        return {
            "call_id": call_id,
            "key": call_key,
            "upper_microusd": upper,
            "input_token_upper": input_upper,
            "max_output_tokens": max_output,
        }

    def settle(self, ticket: dict, usage: dict | None):
        if (
            not usage
            or not isinstance(usage.get("input_tokens"), int)
            or not isinstance(usage.get("output_tokens"), int)
        ):
            self.unknown(ticket)
            return None
        if (
            usage["input_tokens"] < 0
            or usage["output_tokens"] < 0
            or usage["input_tokens"] > ticket["input_token_upper"]
            or usage["output_tokens"] > ticket["max_output_tokens"]
        ):
            self.redis.hset(self.key, "protocol_overrun", 1)
            self.unknown(ticket)
            raise AgentError(
                "model_usage_mismatch", "返回用量超出已验证上限，已停止后续调用。", 503
            )
        estimate = peak_estimate_microusd(usage["input_tokens"], usage["output_tokens"])
        if self.redis.eval(SETTLE, 2, self.key, ticket["key"], estimate) != 1:
            raise AgentError("budget_settlement_failed", "预算结算异常，保留原预留。", 503)
        self.redis.hset(ticket["key"], "usage", json.dumps(usage))
        return estimate

    def unknown(self, ticket):
        # No refund for a request that may have consumed tokens. Never reset on startup.
        if self.redis.hget(ticket["key"], "state") in (b"reserved", "reserved"):
            self.redis.hset(ticket["key"], "state", "unknown")
            self.redis.hincrby(self.key, "calls_unknown", 1)

    def snapshot(self):
        return {
            k.decode() if isinstance(k, bytes) else k: int(v)
            for k, v in self.redis.hgetall(self.key).items()
        }
