"""Strict reviewer input and untrusted judge output; no imported human approvals."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.contracts import ContractModel

Support = Literal["supported", "contradicted", "insufficient"]
Explanation = Literal["reasonable", "unreasonable", "insufficient"]


class Rating(ContractModel):
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    reason: str = Field(min_length=1, max_length=1500)
    evidence_refs: list[str] = Field(min_length=1, max_length=10)


class SupportRating(Rating):
    label: Support


class ExplanationRating(Rating):
    label: Explanation


class Judgment(ContractModel):
    support: SupportRating
    explanation: ExplanationRating
    conclusion: Literal["acceptable", "problematic", "uncertain"]
    needs_review: bool = Field(strict=True)
    suspected_cause: str | None = Field(default=None, max_length=1000)


class RequestID(ContractModel):
    request_id: UUID


class ExecutionJudgeRequest(RequestID):
    calibration_batch_id: UUID


class ExecutionJudgeReview(RequestID):
    support: Support
    explanation: Explanation
    note: str = Field(min_length=1, max_length=2000)
    ai_result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class HumanLabel(RequestID):
    support: Support
    explanation: Explanation
    note: str = Field(min_length=1, max_length=2000)
    sample_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SealLabels(RequestID):
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    independent_from_judge: Literal[True]


class DisagreementReview(RequestID):
    support: Support
    explanation: Explanation
    note: str = Field(min_length=1, max_length=2000)
    original_label_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ai_result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
