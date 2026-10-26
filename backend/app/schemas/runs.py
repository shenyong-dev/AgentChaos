"""Run requests never accept scope, actor, lease, approval or model credentials."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concurrency: int = Field(default=1, ge=1, le=5)
    timeout_seconds: int = Field(default=300, ge=10, le=900)
    max_steps: int = Field(default=100, ge=1, le=500)
    max_model_calls: int = Field(default=20, ge=1, le=100)
    max_tokens: int = Field(default=500_000, ge=1, le=2_000_000)


class RoleBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_microusd: int = Field(default=100_000, ge=0, le=1_000_000)
    case_generator_microusd: Literal[0] = 0
    judge_microusd: Literal[0] = 0


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    target_version_id: int = Field(ge=1)
    case_revision_ids: list[int] = Field(min_length=1, max_length=120)
    repetitions: int = Field(default=1, ge=1, le=5)
    seed: int = Field(default=0, ge=0, le=2_000_000_000)
    limits: Limits = Field(default_factory=Limits)
    budgets: RoleBudgets = Field(default_factory=RoleBudgets)

    @field_validator("case_revision_ids")
    @classmethod
    def unique_cases(cls, value):
        if any(v < 1 for v in value) or len(set(value)) != len(value):
            raise ValueError("Unique positive case revision ids required")
        return sorted(value)


class RecoverExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)


class ExecutionInput(RecoverExecution):
    text: str = Field(min_length=1, max_length=12_000)
