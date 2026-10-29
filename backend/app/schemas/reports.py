from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExportReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expires_hours: int = Field(default=24, ge=1, le=168)


class DefectReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["confirmed", "rejected", "fixed", "verified"]
    note: str = Field(min_length=1, max_length=4000)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    fixed_version_id: int | None = Field(default=None, ge=1)
    verification_execution_id: UUID | None = None
    human_attestation: Literal[True]
