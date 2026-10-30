"""Independent judge authorization; no implicit ledger creation or refunds."""

import json
from uuid import uuid4

from app.agents.budget import peak_estimate_microusd
from app.agents.errors import AgentError
from app.executors.budget import RESERVE, SETTLE, UNKNOWN

AUTHORIZATION = "T12-20261028-judge-open"
PROTOCOL_AUTHORIZATION = "T17B-20261030-judge-050"


class JudgeBudget:
    def __init__(self, redis, settings, batch, record, *, prefix="ac:t12:budget"):
        self.redis, self.settings, self.record = redis, settings, record
        self.key = f"{prefix}:{settings.judge_budget_authorization_id}:judge"
        self.batch_key = self.key + ":batch:" + batch
        self.calls_key = self.batch_key + ":calls"

    def keys(self, ticket):
        return [self.key, self.batch_key, self.calls_key, ticket["key"]]

    def reserve(self, prompt_bytes, max_output):
        s = self.settings
        finite = (
            s.judge_budget_authorization_id == PROTOCOL_AUTHORIZATION
            and not s.judge_budget_unlimited
            and 0 < s.judge_budget_usd <= 0.50
        )
        legacy = s.judge_budget_unlimited and s.judge_budget_authorization_id == AUTHORIZATION
        if (
            not s.judge_allow_paid_model_calls
            or not (finite or legacy)
            or s.judge_model != "deepseek-flash"
        ):
            raise AgentError("judge_not_authorized", "裁判独立开关或本项授权尚未匹配。", 403)
        if prompt_bytes > 32_768 or not 1 <= max_output <= (1536 if finite else 2048):
            raise AgentError("judge_input_limit", "评价输入或输出超过受控大小。", 400)
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
            int(s.judge_budget_usd * 1_000_000) if finite else -1,
            500_000,
            12 if finite else 30,
            1_000_000 if finite else 2_000_000,
            *([12] if finite else []),
        )
        if result != 1:
            raise AgentError(
                "judge_budget_unavailable",
                "账本缺失或本批金额/调用/token上限已到，未发送请求。",
                429,
            )
        # SQL evidence must commit before the provider is contacted.
        self.record("reserved", {"ticket": ticket, "role": "judge"})
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
            raise AgentError("judge_usage_mismatch", "返回用量超过预留假设，停止评价。", 503)
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
            raise AgentError("judge_settlement_failed", "评价用量结算失败，保留预留。", 503)
        if result:
            self.record(
                "settled",
                {
                    "ticket": ticket,
                    "usage": usage,
                    "estimate_microusd": estimate,
                    "role": "judge",
                },
            )
        return estimate

    def unknown(self, ticket):
        if self.redis.eval(UNKNOWN, 4, *self.keys(ticket)):
            self.record("unknown", {"ticket": ticket, "role": "judge"})

    def snapshot(self):
        return {k.decode(): int(v) for k, v in self.redis.hgetall(self.key).items()}
