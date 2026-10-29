"""New T16 caps; reuse immutable ticket settlement, never historical authorization."""

from copy import deepcopy
from uuid import uuid4

from app.agents.budget import peak_estimate_microusd
from app.agents.errors import AgentError
from app.executors.budget import RESERVE, ExecutorBudget
from app.judging.budget import JudgeBudget

AGENT_AUTH = "T16-20261029-agent-54.30"
JUDGE_AUTH = "T16-20261029-judge-1.35"
AGENT_CAP = 54_300_000
JUDGE_CAP = 1_350_000
# Sum of all 320 frozen episode call limits. Per-run limits still apply.
AGENT_RESERVE = RESERVE.replace("calls_started') or '0')>=400", "calls_started') or '0')>=13880")
JUDGE_RESERVE = RESERVE.replace("calls_started') or '0')>=400", "calls_started') or '0')>=90")
assert AGENT_RESERVE != RESERVE and JUDGE_RESERVE != RESERVE


class FormalExecutorBudget(ExecutorBudget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, prefix="ac:t16:budget", **kwargs)

    def reserve(self, prompt_bytes, max_output):
        s = self.settings
        if (
            not s.executor_allow_paid_model_calls
            or s.executor_budget_authorization_id != AGENT_AUTH
            or s.executor_agent_budget_unlimited
            or s.agent_model != "deepseek-flash"
        ):
            raise AgentError("model_not_authorized", "T16独立授权未匹配。", 403)
        if prompt_bytes > 65_536 or not 1 <= max_output <= 4096:
            raise AgentError("model_input_limit", "请求超过受控大小。", 400)
        upper = prompt_bytes * 2 + 8192
        ticket = {
            "call_id": str(uuid4()),
            "input_token_upper": upper,
            "max_output_tokens": max_output,
            "upper_microusd": peak_estimate_microusd(upper, max_output),
        }
        ticket["key"] = f"{self.execution_key}:call:{ticket['call_id']}"
        with self.control.transaction() as (_, run, execution):
            if run.budget_json["authorization_id"] != AGENT_AUTH:
                raise AgentError("budget_authorization_changed", "运行授权不匹配。", 403)
            limits = run.manifest_json["limits"]
            result = self.redis.eval(
                AGENT_RESERVE,
                4,
                *self.keys(ticket),
                ticket["upper_microusd"],
                upper + max_output,
                AGENT_CAP,
                run.budget_json["agent_microusd"],
                limits["max_model_calls"],
                limits["max_tokens"],
            )
            if result != 1:
                raise AgentError(
                    "formal_budget_unavailable", "账本缺失或限额已到，未发送请求。", 429
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


class FormalJudgeBudget(JudgeBudget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, prefix="ac:t16:budget", **kwargs)

    def reserve(self, prompt_bytes, max_output):
        s = self.settings
        if (
            not s.judge_allow_paid_model_calls
            or s.judge_budget_unlimited
            or s.judge_budget_authorization_id != JUDGE_AUTH
            or s.judge_model != "deepseek-flash"
        ):
            raise AgentError("judge_not_authorized", "T16裁判独立授权未匹配。", 403)
        if prompt_bytes > 32_768 or not 1 <= max_output <= 2048:
            raise AgentError("judge_input_limit", "评价输入或输出超过受控大小。", 400)
        tokens = prompt_bytes * 2 + 8192
        ticket = {
            "call_id": str(uuid4()),
            "input_token_upper": tokens,
            "max_output_tokens": max_output,
            "upper_microusd": peak_estimate_microusd(tokens, max_output),
        }
        if ticket["upper_microusd"] > 15_000:
            raise AgentError("judge_episode_budget", "本条预留超过冻结的0.015美元上限。", 429)
        ticket["key"] = self.calls_key + ":" + ticket["call_id"]
        result = self.redis.eval(
            JUDGE_RESERVE,
            4,
            *self.keys(ticket),
            ticket["upper_microusd"],
            tokens + max_output,
            JUDGE_CAP,
            450_000,
            30,
            2_000_000,
        )
        if result != 1:
            raise AgentError(
                "judge_budget_unavailable", "账本缺失或本批限额已到，未发送请求。", 429
            )
        self.record("reserved", {"ticket": ticket, "role": "judge"})
        return ticket
