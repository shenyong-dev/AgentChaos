from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.errors import AgentError
from app.schemas.contracts import TestCaseDraft


class RefundExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["refund_ledger"]
    count: int = Field(ge=0, le=1, strict=True)
    total_fen: int = Field(ge=0, strict=True)
    order_id: str | None = Field(default=None, pattern=r"^O-[0-9]{4}$")


class RefundCase(TestCaseDraft):
    schema_version: Literal["refund-1"] = "refund-1"
    fixture: Literal["refund-reference-v1"] = "refund-reference-v1"
    assertions: list[RefundExpected] = Field(min_length=1, max_length=1)
    task_goal: None = None


def freeze(row, content):
    try:
        draft = RefundCase.model_validate(
            {
                **{
                    k: content[k]
                    for k in (
                        "case_key",
                        "revision",
                        "family_key",
                        "category",
                        "dataset_split",
                        "turns",
                    )
                },
                "reference_clock": content["fixture"]["reference_clock"],
                "fixture": "refund-reference-v1",
                "schema_version": content["expected"]["schema_version"],
                "assertions": content["expected"]["assertions"],
                "review_status": row.review_status,
            }
        ).model_dump(mode="json")
        draft["adapter_type"] = "reference_refund"
        return {
            "case_revision_id": row.id,
            "content_hash": row.content_hash,
            "content": content,
            "dsl": draft,
            "reviewer_id": row.reviewer_id,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        }
    except (ValueError, KeyError, IndexError):
        raise AgentError("refund_case_invalid", "退款用例不满足冻结的领域契约。", 409) from None
