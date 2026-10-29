"""Independent case_generator authorization; no implicit ledger creation or refunds."""

import json
from uuid import uuid4

from app.agents.budget import peak_estimate_microusd
from app.agents.errors import AgentError
from app.executors.budget import RESERVE, SETTLE, UNKNOWN

AUTHORIZATION = "T11-20261028-casegen-open"


class GenerationBudget:
    def __init__(self, redis, settings, batch, record, *, prefix="ac:t11:budget"):
        self.redis, self.settings, self.record = redis, settings, record
        self.key = f"{prefix}:{settings.casegen_budget_authorization_id}:case_generator"
        self.batch_key = self.key + ":batch:" + batch
        self.calls_key = self.batch_key + ":calls"

    def keys(self, ticket):
        return [self.key, self.batch_key, self.calls_key, ticket["key"]]

    def reserve(self, prompt_bytes, max_output):
        s = self.settings
        if (
            not s.casegen_allow_paid_model_calls
            or not s.casegen_budget_unlimited
            or s.casegen_budget_authorization_id != AUTHORIZATION
            or s.casegen_model != "deepseek-flash"
        ):
            raise AgentError("casegen_not_authorized", "T11生成器独立开关或授权尚未匹配。", 403)
        if prompt_bytes > 65_536 or not 1 <= max_output <= 4096:
            raise AgentError("casegen_input_limit", "生成输入或输出超过受控大小。", 400)
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
            -1,
            100_000,
            2,
            200_000,
        )
        if result != 1:
            raise AgentError(
                "casegen_budget_unavailable",
                "账本缺失或本批金额/调用/token上限已到，未发送请求。",
                429,
            )
        # SQL evidence must commit before the provider is contacted.
        self.record("reserved", {"ticket": ticket, "role": "case_generator"})
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
            raise AgentError("casegen_usage_mismatch", "返回用量超过预留假设，停止生成。", 503)
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
            raise AgentError("casegen_settlement_failed", "生成用量结算失败，保留预留。", 503)
        if result:
            self.record(
                "settled",
                {
                    "ticket": ticket,
                    "usage": usage,
                    "estimate_microusd": estimate,
                    "role": "case_generator",
                },
            )
        return estimate

    def unknown(self, ticket):
        if self.redis.eval(UNKNOWN, 4, *self.keys(ticket)):
            self.record("unknown", {"ticket": ticket, "role": "case_generator"})

    def snapshot(self):
        return {k.decode(): int(v) for k, v in self.redis.hgetall(self.key).items()}
