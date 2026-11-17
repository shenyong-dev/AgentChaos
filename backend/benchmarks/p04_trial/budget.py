"""Create-only unlimited cumulative copilot ledger; bounded requests and durable usage."""

import json
from uuid import uuid4

from app.agents.budget import peak_estimate_microusd
from app.agents.errors import AgentError
from app.interactive.budget import LOCAL_RESERVE, InteractionBudget
from benchmarks.t19.collector import exclusive_json

from .package import AUTHORIZATION, ROOT, approval, check

LEDGER = f"ac:p04:budget:{AUTHORIZATION}:copilot"


def binding(redis, package):
    frozen = approval(package)
    endpoint = redis.connection_pool.connection_kwargs
    run_id = redis.info("server").get("run_id")
    if not run_id:
        raise ValueError("Redis identity unavailable")
    return {
        "package_hash": frozen["package_hash"],
        "ledger": LEDGER,
        "redis_run_id": run_id,
        "endpoint": {key: endpoint.get(key) for key in ("host", "port", "db")},
    }


def initialized(redis, package):
    marker = json.loads((ROOT / "initialization.json").read_text(encoding="utf-8"))
    raw = redis.hgetall(LEDGER)
    if (
        marker["binding"] != binding(redis, package)
        or not raw
        or int(raw.get("limit", 0)) != -1
        or raw.get("initialization_nonce") != marker["nonce"]
    ):
        raise ValueError("Pilot ledger or Redis identity changed; never reinitialize")
    return marker


def initialize(redis, package):
    path = ROOT / "initialization.json"
    if path.exists():
        initialized(redis, package)
        return
    marker = {"binding": binding(redis, package), "nonce": str(uuid4())}
    if redis.exists(LEDGER):
        raise ValueError("Unclaimed pilot ledger exists")
    exclusive_json(path, marker)
    if (
        redis.eval(
            "if redis.call('EXISTS',KEYS[1])~=0 then return 0 end; "
            "redis.call('HSET',KEYS[1],'limit',-1,'initialization_nonce',ARGV[1]); return 1",
            1,
            LEDGER,
            marker["nonce"],
        )
        != 1
    ):
        raise ValueError("Initialization uncertain; preserve original marker")


class PilotBudget(InteractionBudget):
    def __init__(self, redis, settings, session_id, package, engine):
        self.redis, self.settings, self.package, self.engine = redis, settings, package, engine
        self.key = LEDGER
        self.session_key = LEDGER + ":session:" + str(session_id)
        self.calls_key = self.session_key + ":calls"
        self.directory = ROOT / "budget-tickets" / str(session_id)

    def reserve(self, prompt_bytes, max_output):
        approval(self.package)
        check(self.package, self.engine, self.settings)
        initialized(self.redis, self.package)
        settings = self.settings
        if (
            settings.copilot_budget_authorization_id != AUTHORIZATION
            or not settings.copilot_allow_paid_model_calls
            or not settings.copilot_local_unlimited_calls
            or settings.copilot_model != "deepseek-flash"
            or settings.copilot_thinking != "disabled"
            or settings.copilot_max_output_tokens != 1536
            or settings.model_timeout_seconds > 45
        ):
            raise AgentError("p04_configuration", "本轮助手配置不匹配，未调用。", 403)
        if int(self.redis.hget(self.key, "calls_unknown") or 0):
            raise AgentError("p04_unknown_usage", "原调用用量未知，请先核查，不发送新请求。", 409)
        if not 0 < prompt_bytes <= 32768 or not 1 <= max_output <= 1536:
            raise AgentError("p04_input_limit", "本次上下文或输出超过限制。", 400)
        tokens = prompt_bytes * 2 + 8192
        ticket = {
            "call_id": str(uuid4()),
            "input_token_upper": tokens,
            "max_output_tokens": max_output,
            "upper_microusd": peak_estimate_microusd(tokens, max_output),
        }
        ticket["key"] = self.calls_key + ":" + ticket["call_id"]
        if (
            self.redis.eval(
                LOCAL_RESERVE, 4, *self.keys(ticket), ticket["upper_microusd"], tokens + max_output
            )
            != 1
        ):
            raise AgentError("p04_ledger_unavailable", "本轮账本不可确认，未调用。", 409)
        try:
            self.record(ticket, "reserved")
        except OSError:
            self.unknown(ticket)
            raise AgentError("p04_ticket_unavailable", "调用票据未保存，预留保留。", 503) from None
        return ticket
