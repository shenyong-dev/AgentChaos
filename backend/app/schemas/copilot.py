"""Typed page context and cited answers; never accept arbitrary destinations/tools."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.guidance import GuideContext


class CopilotContext(GuideContext):
    comparison_run_id: UUID | None = None

    @model_validator(mode="after")
    def comparison_page(self):
        if self.comparison_run_id and (self.page != "reports" or self.object_kind != "run"):
            raise ValueError("Comparison requires an explicitly selected report run")
        return self


class CopilotCreate(CopilotContext):
    request_id: UUID


class CopilotAsk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    question: str = Field(min_length=1, max_length=1500)


class CitedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=600)
    citations: list[str] = Field(min_length=1, max_length=5)


class CopilotAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facts: list[CitedStatement] = Field(max_length=5)
    possible_causes: list[CitedStatement] = Field(max_length=4)
    missing_evidence: list[str | CitedStatement] = Field(max_length=5)
    next_steps: list[CitedStatement] = Field(max_length=4)
    action_ids: list[str] = Field(max_length=4)
    certainty: Literal["limited", "insufficient"]
