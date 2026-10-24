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
    task: Literal["T01"] = "T01"
    api_prefix: str = "/api/v1"
    model_provider: str = "DeepSeek 官方 API"
    model_key_configured: bool
    model_connectivity: Literal["not_probed"] = "not_probed"
    paid_calls_enabled: bool
    run_concurrency: int
    roles: list[ModelRoleConfig]
    test_execution_available: bool = False
