"""Explicit human review of the fixed T13 catalog; no client-supplied approval fields."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DatasetReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    critical_cases: list[str] = Field(min_length=20, max_length=20)
    reviewed_all_cases: Literal[True]
    reviewed_critical_expectations: Literal[True]
    approved_experiment_scale: Literal[True]
    note: str = Field(min_length=10, max_length=4000)
