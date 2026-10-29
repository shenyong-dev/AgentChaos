"""Local settings only; never serialize a password or API key into public evidence."""

import hashlib
import json
import os
from pathlib import Path

from pydantic import SecretStr

from app.core.config import Settings

ROOT = Path(os.environ.get("AC_T16_ROOT", "D:/AgentChaos"))
LOCAL = ROOT / ".local/t16"
EVIDENCE = ROOT / "docs/evidence/T16"


def read(path):
    return json.loads(Path(path).read_bytes())


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        file.write(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def settings(*, version="V0", paid=False):
    private = read(LOCAL / "session-private.json")
    values = {
        **private["mysql"],
        "allow_paid_model_calls": False,
        "executor_allow_paid_model_calls": paid,
        "executor_budget_authorization_id": "T16-20261029-agent-54.30",
        # This is a per-run creation ceiling, not the approval-wide ledger cap.
        "executor_agent_budget_usd": 0.50,
        "executor_agent_budget_unlimited": False,
        "casegen_allow_paid_model_calls": False,
        "casegen_budget_usd": 0,
        "judge_allow_paid_model_calls": False,
        "judge_budget_unlimited": False,
        "judge_budget_usd": 1.35,
        "judge_budget_authorization_id": "T16-20261029-judge-1.35",
        "executor_namespace": "ac:t16:" + private["marker"],
        "run_concurrency": 1,
    }
    values["mysql_password"] = SecretStr(values["mysql_password"])
    # V0 Settings predates this field. Do not add an unobservable V1 setting to it.
    if "agent_tool_policy" in Settings.model_fields:
        values["agent_tool_policy"] = "single_call_v1" if version == "V1" else "baseline"
    result = Settings(_env_file=ROOT / ".env").model_copy(update=values)
    assert result.mysql_database.startswith("agentchaos_t16_formal_")
    assert result.mysql_database != Settings(_env_file=ROOT / ".env").mysql_database
    return result
