"""Explicit team-cloud authorization, separate accounting and admission guard.

No ledger is created by application startup or a request. Local approvals retain
their existing paths; these settings are inert without a matching private record.
"""

import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT

AUTHORIZATION = "T22A-20261111-team-open"
ROLES = ("agent", "case_generator", "judge", "copilot")
FIELDS = {
    "agent": "model_budget_authorization_id",
    "case_generator": "casegen_budget_authorization_id",
    "judge": "judge_budget_authorization_id",
    "copilot": "copilot_budget_authorization_id",
}


def approval_path():
    return PROJECT_ROOT / ".local/cloud/approval.json"


def team_cloud(settings):
    if not (
        settings.app_env == "production"
        and settings.cloud_deployment_id
        and settings.cloud_open_model_budget
        and settings.cloud_approval_sha256
    ):
        return False
    try:
        value = json.loads(approval_path().read_bytes())
        return (
            canonical_hash(value) == settings.cloud_approval_sha256
            and value["authorization"] == AUTHORIZATION
            and value["deployment_id"] == settings.cloud_deployment_id
            and set(value["roles"]) == set(ROLES)
            and value["cumulative_money_limit"] is None
            and value["cumulative_request_limit"] is None
            and value["approved_statement"] == "允许真实调用，不设累计费用上限"
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def authorized(settings, role):
    return (
        team_cloud(settings) and role in FIELDS and getattr(settings, FIELDS[role]) == AUTHORIZATION
    )


def root_key(settings, role):
    if not authorized(settings, role):
        return None
    return f"ac:cloud:{settings.cloud_deployment_id}:budget:{AUTHORIZATION}:{role}"


def executor_authorized(settings):
    return team_cloud(settings) and settings.executor_budget_authorization_id == AUTHORIZATION


def capacity(settings):
    if not settings.cloud_deployment_id:
        return {"applicable": False, "ready": True}
    if not team_cloud(settings) or os.name != "posix":
        return {"applicable": True, "ready": False, "reason": "云端部署授权或资源观察不可确认。"}
    try:
        memory = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[2] == "kB":
                memory[fields[0].rstrip(":")] = int(fields[1]) * 1024
        available = memory.get("MemAvailable")
        free = shutil.disk_usage(PROJECT_ROOT / ".local").free
        ready = available is not None and available >= 256 * 1024**2 and free >= 1024**3
        cgroup_free = None
        cgroup = Path("/sys/fs/cgroup")
        if (cgroup / "memory.max").exists():
            maximum = (cgroup / "memory.max").read_text().strip()
            if maximum != "max":
                cgroup_free = int(maximum) - int((cgroup / "memory.current").read_text())
                ready = ready and cgroup_free >= 32 * 1024**2
        return {
            "applicable": True,
            "ready": ready,
            "available_bytes": available,
            "disk_free_bytes": free,
            "container_available_bytes": cgroup_free,
            "reason": "" if ready else "云端内存或磁盘余量不足，暂不接收新模型调用。",
        }
    except (OSError, ValueError):
        return {
            "applicable": True,
            "ready": False,
            "reason": "无法核对云端资源余量，未发送模型请求。",
        }


def require_capacity(settings):
    value = capacity(settings)
    if not value["ready"]:
        raise AgentError("cloud_capacity_unavailable", value["reason"], 503)


@contextmanager
def invocation_guard(redis, settings, role):
    from uuid import uuid4

    from app.core.cloud_init import verify_ledger

    try:
        verify_ledger(redis, settings, role)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        raise AgentError(
            "cloud_ledger_unavailable", "云端用量记录无法核对，请联系维护者恢复原记录。", 503
        ) from None
    require_capacity(settings)
    key = f"ac:cloud:{settings.cloud_deployment_id}:model-inflight"
    token = uuid4().hex
    # TTL exceeds the bounded provider request; a killed worker cannot retain
    # the lock forever. Compare-and-delete cannot unlock another request.
    if not redis.set(key, token, nx=True, ex=max(180, settings.model_timeout_seconds * 2)):
        raise AgentError("cloud_model_busy", "已有模型请求正在处理，请稍后再试。", 429)
    try:
        yield
    finally:
        redis.eval(
            "if redis.call('GET',KEYS[1])==ARGV[1] then "
            "return redis.call('DEL',KEYS[1]) end return 0",
            1,
            key,
            token,
        )
