"""External dialogue cases accept only bounded reply literal checks."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.errors import AgentError
from app.schemas.assertions import ReplyLiterals
from app.schemas.contracts import TestCaseDraft


class ExternalCase(TestCaseDraft):
    schema_version: Literal["external-dialogue-1"] = "external-dialogue-1"
    fixture: Literal["external-dialogue-v1"] = "external-dialogue-v1"
    assertions: list[ReplyLiterals] = Field(min_length=1, max_length=30)
    task_goal: None = None


class CaseFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_kind: str = Field(pattern=r"^external_http:[a-z][a-z0-9_-]{1,40}$")
    case: ExternalCase


def freeze(row, content):
    try:
        kind = content["fixture"]["adapter_type"]
        draft = CaseFile.model_validate(
            {
                "target_kind": kind,
                "case": {
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
                    "schema_version": content["expected"]["schema_version"],
                    "fixture": content["fixture"]["fixture"],
                    "reference_clock": content["fixture"]["reference_clock"],
                    "assertions": content["expected"]["assertions"],
                    "review_status": row.review_status,
                },
            }
        ).case
        return {
            "case_revision_id": row.id,
            "content_hash": row.content_hash,
            "content": content,
            "dsl": {**draft.model_dump(mode="json"), "adapter_type": kind},
            "reviewer_id": row.reviewer_id,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        }
    except (ValueError, KeyError, TypeError):
        raise AgentError("external_case_invalid", "外部对话用例不满足已登记契约。", 409) from None


def guide(snapshot):
    content = snapshot["content"]
    return {
        "title": content["case_key"],
        "risk": "核对外部回答中的指定文本，不判定业务成功或通用语义质量。",
        "status": "available",
        "policy": "external-dialogue-v1",
        "policy_hash": snapshot["content_hash"],
        "guide_hash": snapshot["content_hash"],
        "case_revision_id": snapshot["case_revision_id"],
        "case_key": content["case_key"],
        "revision": content["revision"],
        "content_hash": snapshot["content_hash"],
        "reviewed_reason": None,
        "missing": [],
        "provenance": content["source"],
        "prerequisites": {
            "fixture": "external-dialogue-v1",
            "reference_clock": content["fixture"]["reference_clock"],
            "knowledge_profile": "not_applicable",
            "default_knowledge_compatible": True,
            "initial_state": None,
            "fault_note": None,
        },
        "turns": [{"number": i, "user": t["user"]} for i, t in enumerate(content["turns"], 1)],
        "expected": {
            "task_goal": None,
            "active_booking_counts": None,
            "permitted_booking_facts": None,
            "manual_checks": [],
            "retrieval_review": None,
            "boundary": "只核对明确文本断言。",
        },
        "assertions": [
            {
                "type": a["type"],
                "title": "回答文本检查",
                "meaning": "包含或排除指定原文",
                "evidence": "外部回答回执",
                "parameters": a,
                "documented": True,
            }
            for a in content["expected"]["assertions"]
        ],
        "run_steps": ["选择外部对象版本", "核对输入与断言", "创建后读取原回执"],
    }
