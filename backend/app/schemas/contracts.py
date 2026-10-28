"""T01 wire contracts only; execution and business assertions arrive in later tasks."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.schemas.assertions import Assertion, BookingCount, BookingGoal, BookingMatches, NoBookingOn


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UserTurn(ContractModel):
    user: str = Field(min_length=1, max_length=12000)


class TestCaseDraft(ContractModel):
    schema_version: Literal["1.0", "1.1"] = "1.0"
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
    task_goal: BookingGoal | None = None

    @model_validator(mode="after")
    def versioned_assertions(self):
        if self.schema_version == "1.0" and (
            self.task_goal
            or any(
                not isinstance(a, (BookingMatches, BookingCount, NoBookingOn))
                for a in self.assertions
            )
        ):
            raise ValueError("Extended predicates and task goals require DSL 1.1")
        return self


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
