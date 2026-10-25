from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.booking import PayloadHash


class DemoSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Reference clock, role, session owner, version and scope are server-controlled.


class DemoMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=12000)


class DemoResume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    decision: Literal["confirm", "reject"]
    draft_id: UUID
    draft_hash: PayloadHash
    confirmation_revision: int = Field(ge=1)


class DemoRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
