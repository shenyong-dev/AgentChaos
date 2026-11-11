from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.assertions import Assertion
from app.schemas.cases import Candidate
from app.schemas.contracts import UserTurn


class VerifiedReductionCandidate(Candidate):
    turns: list[UserTurn] = Field(min_length=1, max_length=50)
    assertions: list[Assertion] = Field(min_length=1, max_length=100)


class ReductionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class ReductionReview(ReductionCreate):
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    keep: list[int] = Field(min_length=1, max_length=49)
    semantic_preserved: bool
    human_attestation: bool
    note: str = Field(min_length=1, max_length=2000)


class ReductionExecute(ReductionCreate):
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    paid_attestation: bool


class ReductionStage(ReductionCreate):
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
