"""Creation is idempotent and version-bound; trusted context is never accepted."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InteractionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    version_id: int = Field(ge=1)
