"""A new finite ledger, gated by a separately recorded human approval."""

import json
import shutil
from uuid import uuid4

from app.agents.budget import peak_estimate_microusd
from app.agents.errors import AgentError
from app.executors.budget import RESERVE
from app.interactive.budget import InteractionBudget

from .preparation import ROOT, check_current, exclusive_json, load_package

AUTHORIZATION = "T19B-20261110-copilot-1"
LEDGER = f"ac:t19b:budget:{AUTHORIZATION}:copilot"
LIMITS = {
    "authorization_id": AUTHORIZATION,
    "role": "copilot",
    "global_microusd": 1000000,
    "global_requests": 72,
    "session_microusd": 100000,
    "session_requests": 12,
    "questions_per_task": 4,
    "requests_per_question": 3,
    "question_seconds": 120,
    "request_seconds": 45,
    "max_output_tokens": 1536,
}


def approval(package):
    frozen = load_package(package)
    path = ROOT / "approval.json"
    if not path.exists():
        raise AgentError("t19b_not_approved", "T19B独立预算尚未确认，未调用模型。", 403)
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("approved_by") != "human_user"
        or not value.get("user_approval_reference")
        or value.get("package_hash") != frozen["package_hash"]
        or value.get("limits") != LIMITS
        or frozen["protocol"]["limits"] != LIMITS
    ):
        raise AgentError("t19b_approval_changed", "T19B授权与冻结方案不匹配。", 403)
    return frozen


def initialization_binding(redis, package):
    frozen = approval(package)
    run_id = redis.info("server").get("run_id")
    if not run_id:
        raise ValueError("Redis instance identity is unavailable")
    connection = redis.connection_pool.connection_kwargs
    return {
        "package_hash": frozen["package_hash"],
        "ledger": LEDGER,
        "redis_run_id": run_id,
        "redis_endpoint": {k: connection.get(k) for k in ("host", "port", "db")},
    }


def check_initialized(redis, package):
    path = ROOT / "initialization.json"
    if not path.exists():
        raise AgentError("t19b_not_initialized", "T19B尚未初始化，未调用模型。", 403)
    marker = json.loads(path.read_text(encoding="utf-8"))
    if marker["binding"] != initialization_binding(redis, package):
        raise ValueError("Redis instance or approval changed; preserve the old ledger")
    raw = redis.hgetall(LEDGER)
    if (
        not raw
        or int(raw.get("limit", 0)) != 1000000
        or raw.get("initialization_nonce") != marker["nonce"]
    ):
        raise ValueError("Trial ledger lost or changed; do not initialize a replacement")
    return marker


def initialize(redis, package):
    binding = initialization_binding(redis, package)
    path = ROOT / "initialization.json"
    if path.exists():
        check_initialized(redis, package)
        return
    if redis.exists(LEDGER):
        raise ValueError("Unclaimed trial ledger already exists; refusing to adopt or reset")
    marker = {"binding": binding, "nonce": str(uuid4())}
    # Write before Redis, create-only. A crash or lost Redis requires manual audit,
    # never a fresh allowance under the same human approval.
    exclusive_json(path, marker)
    result = redis.eval(
        "if redis.call('EXISTS',KEYS[1])~=0 then return 0 end; "
        "redis.call('HSET',KEYS[1],'limit',1000000,'initialization_nonce',ARGV[1]); return 1",
        1,
        LEDGER,
        marker["nonce"],
    )
    if result != 1:
        raise ValueError("Initialization was not completed; preserve marker and audit")


class TrialBudget(InteractionBudget):
    def __init__(self, redis, settings, session_id, package, engine):
        self.redis, self.settings = redis, settings
        self.package, self.engine = package, engine
        self.key = LEDGER
        self.session_key = self.key + ":session:" + str(session_id)
        self.calls_key = self.session_key + ":calls"
        self.directory = ROOT / "budget-tickets" / str(session_id)

    def reserve(self, prompt_bytes, max_output):
        approval(self.package)
        check_current(self.package, self.engine)
        check_initialized(self.redis, self.package)
        s = self.settings
        if (
            s.copilot_budget_authorization_id != AUTHORIZATION
            or not s.copilot_allow_paid_model_calls
            or s.copilot_local_unlimited_calls
            or s.copilot_budget_usd != 1
            or s.copilot_model != "deepseek-flash"
            or s.copilot_thinking != "disabled"
            or s.model_timeout_seconds > 45
            or s.copilot_max_output_tokens != 1536
        ):
            raise AgentError("t19b_configuration_changed", "T19B角色或限额不匹配。", 403)
        if shutil.disk_usage("C:/").free < 2_000_000_000:
            raise AgentError("t19b_disk_low", "系统盘低于实验空间线，未调用模型。", 503)
        if not 0 < prompt_bytes <= 32768 or not 1 <= max_output <= 1536:
            raise AgentError("t19b_input_limit", "T19B上下文或输出超过限制。", 400)
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
            1000000,
            100000,
            12,
            1000000,
            72,
        )
        if result != 1:
            raise AgentError("t19b_budget_unavailable", "T19B额度不可用，未调用模型。", 429)
        try:
            self.record(ticket, "reserved")
        except OSError:
            self.unknown(ticket)
            raise AgentError("t19b_ticket_unavailable", "T19B票据未保存，预留保留。", 503) from None
        return ticket
