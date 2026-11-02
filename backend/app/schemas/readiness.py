"""Timestamped observations and operation prerequisites, never authorization grants."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

State = Literal["ready", "blocked", "unknown", "not_available"]


class ReadinessCheck(BaseModel):
    id: str
    label: str
    status: State
    code: str
    message: str
    next_step: str
    checked_at: datetime
    expires_at: datetime
    source: Literal["live", "configuration", "not_implemented"] = "live"
    facts: dict[str, int | bool | str | None] = Field(default_factory=dict)


class OperationReadiness(BaseModel):
    id: str
    label: str
    status: State
    required_checks: list[str]
    blockers: list[str]
    note: str
    destination: str


class ReadinessReport(BaseModel):
    schema_version: Literal["T17C-1"] = "T17C-1"
    checked_at: datetime
    expires_at: datetime
    checks: list[ReadinessCheck]
    operations: list[OperationReadiness]
    boundary: str = (
        "本页只检查前置条件，不执行实验或模型探测；"
        "实际动作仍由服务端权限、版本、确认及预算守卫决定。"
    )
