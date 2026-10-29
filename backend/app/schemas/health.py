from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ComponentCheck(BaseModel):
    name: Literal["api", "mysql", "redis", "model"]
    status: Literal["ok", "error", "not_configured", "not_probed"]
    code: str
    message: str
    latency_ms: float | None = None
    version: str | None = None
    capabilities: dict[str, bool] = Field(default_factory=dict)


class HealthReport(BaseModel):
    status: Literal["infrastructure_ready", "degraded"]
    checked_at: datetime
    checks: list[ComponentCheck]


class ModelRoleConfig(BaseModel):
    role: Literal["agent", "case_generator", "judge"]
    model: str
    thinking: Literal["enabled", "disabled"]
    max_output_tokens: int
    budget_usd: float


class PublicConfig(BaseModel):
    app_version: str
    environment: str
    task: Literal["T15"] = "T15"
    api_prefix: str = "/api/v1"
    model_provider: str = "DeepSeek 官方 API"
    model_key_configured: bool
    model_connectivity: Literal["not_probed", "historically_verified"] = "not_probed"
    paid_calls_enabled: bool
    run_concurrency: int
    roles: list[ModelRoleConfig]
    test_execution_available: bool = True
    assertion_evaluation_available: bool = True
    executor_paid_calls_enabled: bool = False
    executor_agent_budget_usd: float = 0
    executor_agent_budget_unlimited: bool = False
    event_replay_available: bool = True
    executor_liveness: Literal["not_probed"] = "not_probed"
    agent_demo_available: bool = True
    case_generation_available: bool = True
    casegen_paid_calls_enabled: bool = False
    semantic_judging_available: bool = True
    judge_paid_calls_enabled: bool = False
    judge_budget_unlimited: bool = False
    casegen_budget_unlimited: bool = False
