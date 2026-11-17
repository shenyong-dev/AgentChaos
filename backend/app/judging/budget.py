"""Independent judge authorization; no implicit ledger creation or refunds."""

import json
from uuid import uuid4

from app.agents.budget import peak_estimate_microusd
from app.agents.errors import AgentError
from app.core.cloud import authorized as cloud_authorized
from app.core.cloud import root_key
from app.executors.budget import RESERVE, SETTLE, UNKNOWN

AUTHORIZATION = "T12-20261028-judge-open"
PROTOCOL_AUTHORIZATION = "T17B-20261030-judge-050"


def authorization(settings, schema_version=""):
    """One policy for readiness and reservation; a dataset name is not an approval."""
    s = settings
    finite = (
        s.judge_budget_authorization_id == PROTOCOL_AUTHORIZATION
        and not s.judge_budget_unlimited
        and 0 < s.judge_budget_usd <= 0.50
    )
    cloud = s.judge_budget_unlimited and cloud_authorized(s, "judge")
    legacy = s.judge_budget_unlimited and s.judge_budget_authorization_id == AUTHORIZATION
    if not s.judge_allow_paid_model_calls:
        raise AgentError("judge_not_authorized", "AI裁判尚未启用，请联系维护者。", 403)
    if schema_version.startswith("T17B") and not (finite or cloud):
        raise AgentError(
            "judge_task_authorization", "本批次的AI裁判授权未匹配，请联系维护者。", 403
        )
    if not (finite or cloud or legacy) or s.judge_model != "deepseek-flash":
        raise AgentError("judge_not_authorized", "AI裁判授权或模型配置未匹配。", 403)
    return finite


class JudgeBudget:
    def __init__(self, redis, settings, batch, record, *, prefix="ac:t12:budget", limits=None):
        self.redis, self.settings, self.record = redis, settings, record
        self.limits = limits or {}
        self.key = (
            root_key(settings, "judge")
            or f"{prefix}:{settings.judge_budget_authorization_id}:judge"
        )
        self.batch_key = self.key + ":batch:" + batch
        self.calls_key = self.batch_key + ":calls"

    def keys(self, ticket):
        return [self.key, self.batch_key, self.calls_key, ticket["key"]]

    def caps(self, finite):
        return (
            min(500_000, self.limits.get("microusd", 500_000)),
            min(12 if finite else 30, self.limits.get("calls", 30)),
            min(1_000_000 if finite else 2_000_000, self.limits.get("tokens", 2_000_000)),
        )

    def check_ready(self):
        """Read existing records only. Never initialize, reserve or refund here."""
        from app.core.cloud import require_capacity
        from app.core.cloud_init import verify_ledger

        finite = authorization(self.settings)
        try:
            verify_ledger(self.redis, self.settings, "judge")
            root = self.redis.hgetall(self.key)
            batch = self.redis.hgetall(self.batch_key)
            calls = self.redis.hgetall(self.calls_key)
            cap = int(root.get(b"limit", b"0"))
            money, count, tokens = self.caps(finite)

            def used(row):
                return int(row.get(b"charged_estimate", b"0")) + int(row.get(b"held_upper", b"0"))

            if (
                not cap
                or (cap < 0) == finite
                or b"protocol_overrun" in root
                or finite
                and (
                    used(root) >= min(cap, int(self.settings.judge_budget_usd * 1_000_000))
                    or int(root.get(b"calls_started", b"0")) >= 12
                )
                or used(batch) >= money
                or int(calls.get(b"calls_started", b"0")) >= count
                or int(calls.get(b"token_upper", b"0")) >= tokens
            ):
                raise AgentError(
                    "judge_budget_unavailable", "用量记录缺失或本批额度已到，暂不可运行。", 429
                )
        except AgentError:
            raise
        except Exception:
            raise AgentError(
                "judge_ledger_unavailable", "无法核对原用量记录，请联系维护者。", 503
            ) from None
        require_capacity(self.settings)
        if not self.settings.deepseek_api_key.get_secret_value():
            raise AgentError("model_key_missing", "AI裁判的模型密钥尚未配置。", 503)
        if self.settings.judge_thinking != "disabled":
            raise AgentError("model_mode_unverified", "AI裁判的模型模式尚未核验。", 400)

    def reserve(self, prompt_bytes, max_output):
        s = self.settings
        finite = authorization(s)
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
            *self.caps(finite),
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
