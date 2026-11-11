"""Explicit append-only registration of an owned target's inspected configuration."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RegisterVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: int = Field(ge=1, strict=True)
    expected_config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_version_id: int | None = Field(default=None, ge=1, strict=True)
    confirmed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def confirmation(self):
        if not self.confirmed:
            raise ValueError("请明确确认追加版本快照")
        return self
