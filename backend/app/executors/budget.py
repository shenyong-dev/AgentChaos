"""Approval-wide atomic cap plus per-run/execution limits and MySQL ticket mirrors.

Redis is a non-expiring coordination ledger, not a cache. A missing authorization
fails closed; starting another database/container must never initialize it again.
"""

import json
from copy import deepcopy
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agents.budget import peak_estimate_microusd
from app.agents.errors import AgentError
from app.executors.control import locked_pair

AUTHORIZATION = "T05-20261026-agent-1"
OPEN_AUTHORIZATION = "T06-20261026-agent-open"
FAULT_AUTHORIZATION = "T07-20261027-agent-open"
DIALOGUE_AUTHORIZATION = "T09-20261028-agent-open"
METAMORPHIC_AUTHORIZATION = "T10-20261028-agent-open"


def authorization_limit(settings):
    """-1 explicitly means the user's open T06 approval; zero never means unlimited."""
    if settings.executor_budget_authorization_id in (
        OPEN_AUTHORIZATION,
        FAULT_AUTHORIZATION,
        DIALOGUE_AUTHORIZATION,
        METAMORPHIC_AUTHORIZATION,
    ):
        return -1 if settings.executor_agent_budget_unlimited else 0
    if (
        settings.executor_budget_authorization_id == AUTHORIZATION
        and not settings.executor_agent_budget_unlimited
        and 0 < settings.executor_agent_budget_usd <= 1
    ):
        return int(settings.executor_agent_budget_usd * 1_000_000)
    return 0


RESERVE = """
local cap=tonumber(redis.call('HGET',KEYS[1],'limit') or '0')
if cap==0 or redis.call('HGET',KEYS[1],'protocol_overrun') then return -1 end
local requested=tonumber(ARGV[3])
if (cap<0)~=(requested<0) then return -1 end
local upper=tonumber(ARGV[1]); local tokens=tonumber(ARGV[2])
local function used(key)
 return tonumber(redis.call('HGET',key,'charged_estimate') or '0')+
        tonumber(redis.call('HGET',key,'held_upper') or '0')
end
if (cap>0 and used(KEYS[1])+upper>math.min(cap,requested)) or
   used(KEYS[2])+upper>tonumber(ARGV[4]) or
   tonumber(redis.call('HGET',KEYS[3],'calls_started') or '0')>=tonumber(ARGV[5]) or
   tonumber(redis.call('HGET',KEYS[3],'token_upper') or '0')+tokens>tonumber(ARGV[6]) or
   (cap>0 and tonumber(redis.call('HGET',KEYS[1],'calls_started') or '0')>=400) then return 0 end
for i=1,3 do
 redis.call('HINCRBY',KEYS[i],'held_upper',upper)
 redis.call('HINCRBY',KEYS[i],'calls_started',1)
 redis.call('HINCRBY',KEYS[i],'token_upper',tokens)
end
redis.call('HSET',KEYS[4],'state','reserved','upper',upper,'tokens',tokens)
return 1
"""
SETTLE = """
local state=redis.call('HGET',KEYS[4],'state')
if state=='settled' then return 0 end
if state~='reserved' and state~='unknown' then return -1 end
local upper=tonumber(redis.call('HGET',KEYS[4],'upper'))
local tokens=tonumber(redis.call('HGET',KEYS[4],'tokens'))
for i=1,3 do
 redis.call('HINCRBY',KEYS[i],'held_upper',-upper)
 redis.call('HINCRBY',KEYS[i],'charged_estimate',ARGV[1])
 redis.call('HINCRBY',KEYS[i],'token_upper',tonumber(ARGV[2])-tokens)
 redis.call('HINCRBY',KEYS[i],'calls_completed',1)
 if state=='unknown' then redis.call('HINCRBY',KEYS[i],'calls_unknown',-1) end
end
redis.call('HSET',KEYS[4],'state','settled','estimate',ARGV[1],'usage',ARGV[3])
return 1
"""
UNKNOWN = """
if redis.call('HGET',KEYS[4],'state')~='reserved' then return 0 end
redis.call('HSET',KEYS[4],'state','unknown')
for i=1,3 do redis.call('HINCRBY',KEYS[i],'calls_unknown',1) end
return 1
"""


class ExecutorBudget:
    def __init__(
        self, redis, settings, control, run_uuid, execution_uuid, *, prefix="ac:t05:budget"
    ):
        self.redis, self.settings, self.control = redis, settings, control
        self.key = f"{prefix}:{settings.executor_budget_authorization_id}:agent"
        self.run_key = f"{self.key}:run:{run_uuid}"
        self.execution_key = f"{self.run_key}:execution:{execution_uuid}"

    def keys(self, ticket):
        return [self.key, self.run_key, self.execution_key, ticket["key"]]

    def reserve(self, prompt_bytes, max_output):
        s = self.settings
        if (
            not s.executor_allow_paid_model_calls
            or authorization_limit(s) == 0
            or s.agent_model != "deepseek-flash"
        ):
            raise AgentError("model_not_authorized", "当前任务的模型调用未获准。", 403)
        if prompt_bytes > 65_536 or not 1 <= max_output <= 4096:
            raise AgentError("model_input_limit", "请求超过模型输入或输出限制。", 400)
        input_upper = prompt_bytes * 2 + 8192
        ticket = {
            "call_id": str(uuid4()),
            "input_token_upper": input_upper,
            "max_output_tokens": max_output,
            "upper_microusd": peak_estimate_microusd(input_upper, max_output),
        }
        ticket["key"] = f"{self.execution_key}:call:{ticket['call_id']}"
        with self.control.transaction() as (_, run, e):
            limits = run.manifest_json["limits"]
            if run.budget_json["authorization_id"] != s.executor_budget_authorization_id:
                raise AgentError(
                    "budget_authorization_changed", "运行预算授权与当前配置不一致。", 403
                )
            result = self.redis.eval(
                RESERVE,
                4,
                *self.keys(ticket),
                ticket["upper_microusd"],
                input_upper + max_output,
                authorization_limit(s),
                run.budget_json["agent_microusd"],
                limits["max_model_calls"],
                limits["max_tokens"],
            )
            if result != 1:
                code = "budget_ledger_unavailable" if result == -1 else "model_budget_exhausted"
                raise AgentError(code, "预算账本缺失或预算/调用/token上限已到，未发送请求。", 429)
            usage = deepcopy(e.usage_json or {"tickets": {}})
            usage["tickets"][ticket["call_id"]] = {
                **ticket,
                "state": "reserved",
                "role": "agent",
                "attempt": e.attempt_no,
            }
            e.usage_json = usage
            self._mirror(run, e)
        return ticket

    def _mirror(self, run, e):
        usage = deepcopy(e.usage_json or {})
        usage["roles"] = {
            "agent": self._snapshot(self.execution_key),
            "case_generator": {"calls_started": 0},
            "judge": {"calls_started": 0},
        }
        e.usage_json = usage
        run.summary_json = {
            **(run.summary_json or {}),
            "ledger": self._snapshot(self.run_key),
            "authorization_ledger": self.snapshot(),
        }

    def settle(self, ticket, usage):
        if not usage or not all(
            type(usage.get(k)) is int for k in ("input_tokens", "output_tokens")
        ):
            self.unknown(ticket)
            return None
        if (
            not 0 <= usage["input_tokens"] <= ticket["input_token_upper"]
            or not 0 <= usage["output_tokens"] <= ticket["max_output_tokens"]
        ):
            self.redis.hset(self.key, "protocol_overrun", 1)
            self.unknown(ticket)
            raise AgentError("model_usage_mismatch", "模型用量超出预留假设，停止后续调用。", 503)
        estimate = peak_estimate_microusd(usage["input_tokens"], usage["output_tokens"])
        with Session(self.control.engine) as session, session.begin():
            run, e = locked_pair(session, self.control.execution_id)
            saved = deepcopy(e.usage_json)
            stored = saved["tickets"].get(ticket["call_id"])
            if stored is None or stored["key"] != ticket["key"]:
                raise AgentError("budget_ticket_missing", "用量票据不存在，保留预留。", 503)
            if (
                self.redis.eval(
                    SETTLE,
                    4,
                    *self.keys(ticket),
                    estimate,
                    usage["input_tokens"] + usage["output_tokens"],
                    json.dumps(usage),
                )
                < 0
            ):
                raise AgentError("budget_settlement_failed", "结算失败，保留未知预留。", 503)
            stored.update(state="settled", usage=usage, estimate_microusd=estimate)
            e.usage_json = saved
            self._mirror(run, e)
        return estimate

    def unknown(self, ticket):
        self.redis.eval(UNKNOWN, 4, *self.keys(ticket))
        with Session(self.control.engine) as session, session.begin():
            run, e = locked_pair(session, self.control.execution_id)
            usage = deepcopy(e.usage_json)
            stored = usage["tickets"].get(ticket["call_id"])
            if stored and stored["state"] != "settled":
                stored["state"] = "unknown"
            e.usage_json = usage
            self._mirror(run, e)

    def _snapshot(self, key):
        return {
            (k.decode() if isinstance(k, bytes) else k): int(v)
            for k, v in self.redis.hgetall(key).items()
        }

    def snapshot(self):
        return self._snapshot(self.key)
