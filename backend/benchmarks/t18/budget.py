"""T18 authorization; existing settlement/unknown accounting remains unchanged."""

from copy import deepcopy
from uuid import uuid4

from app.agents.budget import peak_estimate_microusd
from app.agents.errors import AgentError
from app.executors.budget import RESERVE, ExecutorBudget
from benchmarks.t17l.common import disk_guard, guard

from .common import AUTHORIZATION, LEDGER


def initialize_ledger(redis):
    # Atomic first creation. Never reset existing usage, tickets or held reservations.
    result = redis.eval(
        "if redis.call('EXISTS',KEYS[1])==0 then "
        "redis.call('HSET',KEYS[1],'limit',1500000) end "
        "return redis.call('HGET',KEYS[1],'limit')",
        1,
        LEDGER,
    )
    if int(result) != 1_500_000:
        raise RuntimeError("Existing T18 ledger has a different limit; refusing mutation")


class BenchmarkBudget(ExecutorBudget):
    def __init__(self, redis, settings, control, run_uuid, execution_uuid):
        guard(control.engine, settings)
        super().__init__(redis, settings, control, run_uuid, execution_uuid, prefix="ac:t18:budget")

    def reserve(self, prompt_bytes, max_output):
        disk_guard()
        s = self.settings
        guard(self.control.engine, s)
        if (
            not s.executor_allow_paid_model_calls
            or s.executor_budget_authorization_id != AUTHORIZATION
            or s.executor_agent_budget_unlimited
            or s.executor_agent_budget_usd != 1.5
            or s.agent_model != "deepseek-flash"
            or s.agent_thinking != "disabled"
            or s.model_timeout_seconds > 45
        ):
            raise AgentError("model_not_authorized", "T18独立授权或角色设置不匹配。", 403)
        if not 0 < prompt_bytes <= 65536 or not 1 <= max_output <= 1024:
            raise AgentError("model_input_limit", "T18输入/输出限制已到。", 400)
        upper = prompt_bytes * 2 + 8192
        ticket = {
            "call_id": str(uuid4()),
            "input_token_upper": upper,
            "max_output_tokens": max_output,
            "upper_microusd": peak_estimate_microusd(upper, max_output),
        }
        ticket["key"] = f"{self.execution_key}:call:{ticket['call_id']}"
        with self.control.transaction() as (_, run, execution):
            limits = run.manifest_json["limits"]
            if run.budget_json["authorization_id"] != AUTHORIZATION:
                raise AgentError("budget_authorization_changed", "T18运行授权不匹配。", 403)
            result = self.redis.eval(
                RESERVE,
                4,
                *self.keys(ticket),
                ticket["upper_microusd"],
                upper + max_output,
                1_500_000,
                min(run.budget_json["agent_microusd"], 100_000),
                min(limits["max_model_calls"], 20),
                limits["max_tokens"],
                160,
            )
            if result != 1:
                raise AgentError(
                    "model_budget_exhausted", "T18账本缺失或金额/次数已到，未发送。", 429
                )
            usage = deepcopy(execution.usage_json or {"tickets": {}})
            usage["tickets"][ticket["call_id"]] = {
                **ticket,
                "state": "reserved",
                "role": "agent",
                "attempt": execution.attempt_no,
            }
            execution.usage_json = usage
            self._mirror(run, execution)
        return ticket
