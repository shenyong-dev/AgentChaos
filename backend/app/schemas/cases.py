"""Generator output contains proposals only. Identity, split and approval are server fields."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.adapters.refund_cases import RefundExpected
from app.schemas.assertions import Assertion, BookingGoal
from app.schemas.contracts import ContractModel, UserTurn

Profile = Literal["normal", "capacity", "correction"]
RefundProfile = Literal[
    "refund_normal",
    "refund_ineligible",
    "refund_owner",
    "refund_change",
    "refund_decline",
    "refund_duplicate",
]
BOOKING_PROFILES = ("normal", "capacity", "correction")
REFUND_PROFILES = (
    "refund_normal",
    "refund_ineligible",
    "refund_owner",
    "refund_change",
    "refund_decline",
    "refund_duplicate",
)


class GenerateCandidates(ContractModel):
    request_id: UUID
    target: Literal["booking", "refund"] = "booking"
    profiles: list[Profile | RefundProfile] = Field(
        default_factory=lambda: ["normal", "capacity", "correction"], min_length=1, max_length=3
    )

    @model_validator(mode="before")
    @classmethod
    def domain_defaults(cls, value):
        if isinstance(value, dict) and value.get("target") == "refund" and "profiles" not in value:
            return {**value, "profiles": ["refund_normal", "refund_ineligible", "refund_change"]}
        return value

    @model_validator(mode="after")
    def unique(self):
        if len(self.profiles) != len(set(self.profiles)):
            raise ValueError("Profiles must be unique")
        allowed = BOOKING_PROFILES if self.target == "booking" else REFUND_PROFILES
        if set(self.profiles) - set(allowed):
            raise ValueError("Profiles must belong to the selected target")
        return self


class Candidate(ContractModel):
    profile: Profile
    title: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=1200)
    turns: list[UserTurn] = Field(min_length=1, max_length=5)
    assertions: list[Assertion] = Field(min_length=1, max_length=15)
    task_goal: BookingGoal | None = None


class RefundCandidate(ContractModel):
    profile: RefundProfile
    title: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=1200)
    turns: list[UserTurn] = Field(min_length=1, max_length=5)
    assertions: list[RefundExpected] = Field(min_length=1, max_length=1)


class CandidateReview(ContractModel):
    request_id: UUID
    decision: Literal["approve", "edit", "reject"]
    note: str = Field(min_length=1, max_length=2000)
    expected_candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    edited: Candidate | RefundCandidate | None = None

    @model_validator(mode="after")
    def explicit_edit(self):
        if (self.decision == "edit") != (self.edited is not None):
            raise ValueError("Only edit decisions must contain an edited candidate")
        return self
