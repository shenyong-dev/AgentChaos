"""Generator output contains proposals only. Identity, split and approval are server fields."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.assertions import Assertion, BookingGoal
from app.schemas.contracts import ContractModel, UserTurn

Profile = Literal["normal", "capacity", "correction"]


class GenerateCandidates(ContractModel):
    request_id: UUID
    profiles: list[Profile] = Field(
        default_factory=lambda: ["normal", "capacity", "correction"], min_length=1, max_length=3
    )

    @model_validator(mode="after")
    def unique(self):
        if len(self.profiles) != len(set(self.profiles)):
            raise ValueError("Profiles must be unique")
        return self


class Candidate(ContractModel):
    profile: Profile
    title: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=1200)
    turns: list[UserTurn] = Field(min_length=1, max_length=5)
    assertions: list[Assertion] = Field(min_length=1, max_length=15)
    task_goal: BookingGoal | None = None


class CandidateReview(ContractModel):
    request_id: UUID
    decision: Literal["approve", "edit", "reject"]
    note: str = Field(min_length=1, max_length=2000)
    expected_candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    edited: Candidate | None = None

    @model_validator(mode="after")
    def explicit_edit(self):
        if (self.decision == "edit") != (self.edited is not None):
            raise ValueError("Only edit decisions must contain an edited candidate")
        return self
