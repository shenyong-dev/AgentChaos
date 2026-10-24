"""T01 wire contracts only; execution and business assertions arrive in later tasks."""

from datetime import date as Date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UserTurn(ContractModel):
    user: str = Field(min_length=1, max_length=12000)


class BookingMatches(ContractModel):
    type: Literal["booking_matches"]
    date: Date
    attendees: int | None = Field(default=None, ge=1)
    room_code: str | None = None


class BookingCount(ContractModel):
    type: Literal["booking_count"]
    equals: int = Field(ge=0)


class NoBookingOn(ContractModel):
    type: Literal["no_booking_on"]
    date: Date


Assertion = Annotated[BookingMatches | BookingCount | NoBookingOn, Field(discriminator="type")]


class TestCaseDraft(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    case_key: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,79}$")
    revision: int = Field(ge=1)
    family_key: str = Field(min_length=1, max_length=100)
    category: Literal[
        "normal", "tool_execution", "memory_and_tool", "retrieval", "security", "robustness"
    ]
    dataset_split: Literal["dev", "calibration", "holdout"]
    reference_clock: AwareDatetime
    fixture: str = Field(min_length=1, max_length=100)
    turns: list[UserTurn] = Field(min_length=1, max_length=50)
    assertions: list[Assertion] = Field(min_length=1, max_length=100)
    review_status: Literal["draft", "approved", "rejected"] = "draft"


class TraceEvent(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    event_id: UUID
    run_id: UUID
    execution_id: UUID
    attempt: int = Field(ge=1)
    seq: int = Field(ge=1)
    type: Literal[
        "run.started",
        "run.completed",
        "execution.started",
        "execution.completed",
        "node.started",
        "node.completed",
        "retrieval.completed",
        "tool.requested",
        "tool.completed",
        "fault.injected",
        "state.changed",
        "assertion.completed",
        "budget.exceeded",
        "execution.cancelled",
        "execution.error",
    ]
    node: str | None = None
    node_visit: int | None = Field(default=None, ge=1)
    tool_call_id: str | None = None
    parent_event_id: UUID | None = None
    timestamp: AwareDatetime
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
