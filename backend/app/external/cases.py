"""Versioned assertions for external conversations and their evidence."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from app.agents.errors import AgentError
from app.external.schema import PathField, Strict
from app.external.self_service import SELF_PREFIX, is_self
from app.schemas.assertions import ReplyLiterals
from app.schemas.contracts import TestCaseDraft


class ExternalCase(TestCaseDraft):
    schema_version: Literal["external-dialogue-1"] = "external-dialogue-1"
    fixture: Literal["external-dialogue-v1"] = "external-dialogue-v1"
    assertions: list[ReplyLiterals] = Field(min_length=1, max_length=30)
    task_goal: None = None


Scalar = Annotated[str, Field(max_length=320)] | StrictInt | StrictBool | None
DocumentId = Annotated[str, Field(min_length=1, max_length=200)]


class RetrievalTopK(Strict):
    type: Literal["external_retrieval_top_k"]
    document_ids: list[DocumentId] = Field(min_length=1, max_length=20)
    k: int = Field(ge=1, le=20)
    turn: int | None = Field(default=None, ge=1, le=50)

    @model_validator(mode="after")
    def valid_top_k(self):
        if len(set(self.document_ids)) != len(self.document_ids) or len(self.document_ids) > self.k:
            raise ValueError("Expected unique document IDs must fit within top K")
        return self


class ToolStep(Strict):
    name: str = Field(min_length=1, max_length=120)
    arguments: dict[Annotated[str, Field(min_length=1, max_length=64)], Scalar] = Field(
        default_factory=dict, max_length=8
    )


class ToolSequence(Strict):
    type: Literal["external_tool_sequence"]
    steps: list[ToolStep] = Field(min_length=1, max_length=12)
    turn: int | None = Field(default=None, ge=1, le=50)


class BusinessField(Strict):
    type: Literal["external_fact_equals"]
    path: PathField
    value: Scalar
    phase: Literal["before", "after"] = "after"


ExternalAssertion = Annotated[
    ReplyLiterals | RetrievalTopK | ToolSequence | BusinessField, Field(discriminator="type")
]
ASSERTION_LABELS = {
    "reply_literals": ("回复正文", "核对指定文字是否出现", "外部回复"),
    "external_retrieval_top_k": ("检索命中", "核对前 K 条文档 ID", "检索来源"),
    "external_tool_sequence": ("工具顺序与参数", "核对工具调用顺序和指定参数", "OpenTelemetry"),
    "external_fact_equals": ("业务状态", "核对独立查询返回的字段值", "只读查证接口"),
}


class ReliabilityCase(ExternalCase):
    schema_version: Literal["external-reliability-1"] = "external-reliability-1"
    assertions: list[ExternalAssertion] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def turn_references(self):
        if any((getattr(item, "turn", None) or 0) > len(self.turns) for item in self.assertions):
            raise ValueError("The evidence turn must exist in this case")
        return self


class CaseFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_kind: str = Field(pattern=r"^external_http:[a-z][a-z0-9_-]{1,40}$")
    case: ExternalCase | ReliabilityCase


def personal_case(row, owner):
    kind = row.fixture_json.get("adapter_type")
    return (
        row.review_status == "draft"
        and row.dataset_split == "dev"
        and row.fixture_json.get("fixture") == "external-dialogue-v1"
        and is_self(kind)
        and row.source == f"external_import:{kind}:owner:{owner}"
    )


def personal_case_query(row, owner):
    # Match only the reserved personal-ID namespace inside the existing case source field.
    source = ("external_import:" + SELF_PREFIX).replace("_", "!_") + "_" * 32
    return (
        row.source.like(source + f":owner:{owner}", escape="!")
        & (row.review_status == "draft")
        & (row.dataset_split == "dev")
    )


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
    enhanced = content["expected"]["schema_version"] == "external-reliability-1"
    return {
        "title": content["case_key"],
        "risk": "根据用例核对回复、工具调用、检索或业务证据。"
        if enhanced else "核对外部回答中的指定文本。",
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
            "boundary": "规则结论依据本次保留的证据。",
        },
        "assertions": [
            {
                "type": a["type"],
                "title": ASSERTION_LABELS[a["type"]][0],
                "meaning": ASSERTION_LABELS[a["type"]][1],
                "evidence": ASSERTION_LABELS[a["type"]][2],
                "parameters": a,
                "documented": True,
            }
            for a in content["expected"]["assertions"]
        ],
        "run_steps": ["选择外部对象版本", "核对输入与断言", "创建后读取原回执"],
    }
