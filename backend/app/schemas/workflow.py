"""Explicit run intent and an owner-bound short-lived preflight receipt."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.runs import CreateRun


class RunPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration: CreateRun
    action: Literal["new", "rerun", "copy"] = "new"
    source_run_id: UUID | None = None
    dispatch: Literal["ready", "queue_only"] = "ready"

    @model_validator(mode="after")
    def source_required(self):
        if (self.action == "new") != (self.source_run_id is None):
            raise ValueError("Only rerun/copy require an owned source run")
        return self


class CommitPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    plan_id: str = Field(pattern=r"^[a-f0-9]{64}$")
