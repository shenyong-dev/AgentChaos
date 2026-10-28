"""Run-bound experiment faults; never arbitrary scripts, SQL, tools or payloads."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

READ_TOOLS = {"retrieve_rules", "query_availability", "lookup_booking_by_request"}
STAGES = {"F01": "PRE_DISPATCH", "F02": "PRE_COMMIT", "F03": "POST_RESULT", "F04": "POST_COMMIT"}


class FaultParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger_on_nth: int = Field(default=1, ge=1, le=50, strict=True)
    trigger_window: int = Field(default=1, ge=1, le=10, strict=True)
    delay_ms: int = Field(default=0, ge=0, le=500, strict=True)


class FaultSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_key: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{2,79}$")
    revision: int = Field(default=1, ge=1, strict=True)
    fault_type: Literal["F01", "F02", "F03", "F04"]
    trigger_stage: Literal["PRE_DISPATCH", "PRE_COMMIT", "POST_RESULT", "POST_COMMIT"]
    target_tool: str
    parameters: FaultParameters = Field(default_factory=FaultParameters)
    seed: int = Field(default=0, ge=0, le=2_000_000_000, strict=True)
    max_triggers: int = Field(default=1, ge=1, le=5, strict=True)
    enabled_for_test: bool = True

    @model_validator(mode="after")
    def supported(self):
        if self.trigger_stage != STAGES[self.fault_type]:
            raise ValueError("Fault stage does not match its declared effect")
        allowed = (
            READ_TOOLS
            if self.fault_type == "F03"
            else (
                READ_TOOLS | {"confirm_booking"}
                if self.fault_type == "F01"
                else {"confirm_booking"}
            )
        )
        if self.target_tool not in allowed:
            raise ValueError("Unsupported target tool for this fault; F03 is read-only")
        if self.fault_type != "F01" and self.parameters.delay_ms:
            raise ValueError("Delay only applies to F01")
        return self
