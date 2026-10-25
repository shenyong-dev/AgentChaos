"""Whitelisted tools bind server context and user confirmation, never model authority."""

import json
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.errors import AgentError
from app.agents.state import FieldPatch, booking_request, patch_fields
from app.core.config import PROJECT_ROOT
from app.schemas.booking import (
    AvailabilityQuery,
    ConfirmBooking,
    ConfirmBookingToolInput,
    PreviewBooking,
)
from app.services.booking import BookingContext, BookingError
from app.tools.booking import booking_tool_contracts


class RuleQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=1000)


class RequestLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID


class ToolRegistry:
    def __init__(self, booking, retriever):
        self.booking, self.retriever = booking, retriever
        self.inputs = {
            "update_booking_request": FieldPatch,
            "retrieve_rules": RuleQuery,
            "query_availability": AvailabilityQuery,
            "preview_booking": PreviewBooking,
            "confirm_booking": ConfirmBookingToolInput,
            "lookup_booking_by_request": RequestLookup,
        }
        descriptions = {
            "update_booking_request": "提取当前用户明确给出的字段，"
            "每字段必须提供原文source_quotes；缺失不猜测。",
            "retrieve_rules": "检索冻结的实验规则条款，返回来源/修订/有效条件；无依据明确返回。",
            "query_availability": "只读查询当前结构化需求的场地空闲；必须与已提取字段一致。",
            "preview_booking": "生成完整待确认草稿并撤销本会话旧稿；不创建预约。",
            "confirm_booking": "合法用户确认后提交当前草稿，复用原幂等标识；模型不能授权自己。",
            "lookup_booking_by_request": "按原幂等标识查证当前身份的预约事实；暂未查到不证明失败。",
        }
        self.schemas = [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": descriptions[name],
                    "parameters": input_model.model_json_schema(),
                },
            }
            for name, input_model in self.inputs.items()
        ]
        self.manifest = {
            "version": "booking-registry-v1",
            "tools": booking_tool_contracts(),
            "state_tool": FieldPatch.model_json_schema(),
            "retrieval_tool": RuleQuery.model_json_schema(),
            "max_response_bytes": 16384,
            "automatic_write_retries": 0,
        }

    @staticmethod
    def context(state):
        return BookingContext(
            state["scope_id"],
            state["actor_key"],
            datetime.fromisoformat(state["reference_clock"]),
            state["session_id"],
        )

    def dispatch(self, state, name: str, args: dict):
        evidence_id = str(uuid4())
        before = {
            "confirmation_state": state.get("confirmation_state"),
            "confirmation_revision": state.get("confirmation_revision", 0),
        }
        try:
            if name not in self.inputs:
                raise AgentError("tool_not_allowed", "模型请求了未开放的工具。", 403)
            values = self.inputs[name].model_validate(args)
            result = self._dispatch(state, name, values)
        except (AgentError, BookingError) as exc:
            result = {
                "status": "error",
                "code": exc.code,
                "message": exc.message,
                "commit_state": getattr(exc, "commit_state", "not_applicable"),
                "retryable": getattr(exc, "retryable", False),
            }
            key = getattr(exc, "request_key", None)
            if key:
                result["request_key"] = str(key)
            if name == "confirm_booking" and result["commit_state"] == "unknown":
                state["unknown_submission"] = {
                    "request_key": str(key)
                    if key
                    else state.get("pending_draft", {}).get("suggested_idempotency_key")
                }
        except ValidationError as exc:
            result = {
                "status": "error",
                "code": "tool_arguments_invalid",
                "message": "工具参数不符合Schema；身份和授权不能由模型提供。",
                "commit_state": "not_applicable",
                "retryable": False,
                "invalid_fields": [
                    {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
                    for error in exc.errors(include_input=False, include_url=False)
                ],
            }
        directory = PROJECT_ROOT / ".local/t04/tool-evidence"
        directory.mkdir(parents=True, exist_ok=True)
        raw = {
            "evidence_id": evidence_id,
            "name": name,
            "args": args,
            "result": result,
            "source_turn": state["turn_index"],
            "before": before,
            "after": {
                "confirmation_state": state.get("confirmation_state"),
                "confirmation_revision": state.get("confirmation_revision", 0),
            },
        }
        serialized = json.dumps(raw, ensure_ascii=False, indent=2, default=str)
        (directory / f"{evidence_id}.json").write_text(serialized + "\n", encoding="utf-8")
        visible = json.dumps(result, ensure_ascii=False, default=str)
        if len(visible.encode()) > 16384:
            result = {
                "status": "error",
                "code": "tool_output_limit",
                "message": "结果超过本轮工具返回上限，原始结果已保留。",
            }
        state["last_tool_result"] = result
        state["audit"].append(
            {
                "type": "tool",
                "name": name,
                "arguments": args,
                "result": result,
                "evidence_id": evidence_id,
                "source_turn": state["turn_index"],
            }
        )
        return result

    def _dispatch(self, state, name, values):
        context = self.context(state)
        if name == "update_booking_request":
            if state.get("unknown_submission"):
                raise AgentError("submission_unknown", "先用原请求标识查证，不能改变未查清的提交。")
            fields, changed = patch_fields(state, values)
            if changed:
                self.booking.revoke_pending_drafts(context)
                if state.get("business_result"):
                    state["business_history"] = state.get("business_history", []) + [
                        state["business_result"]
                    ]
                state.update(
                    {
                        "fields": fields,
                        "pending_draft": None,
                        "grant": None,
                        "confirmation_state": "rejected"
                        if state.get("confirmation_state") == "rejected"
                        else "idle",
                        "candidate_rooms": [],
                        "business_result": None,
                        "confirmation_revision": state.get("confirmation_revision", 0) + 1,
                    }
                )
            else:
                state["fields"] = fields
            return {"status": "ok", "fields": fields, "changed": changed}
        if name == "retrieve_rules":
            result = self.retriever.retrieve(values.query, context.reference_clock)
            state["retrieval_result"] = result
            refs = [chunk["chunk_id"] for chunk in result["chunks"]]
            state["evidence_refs"] = list(dict.fromkeys(state.get("evidence_refs", []) + refs))
            state["audit"].append(
                {"type": "retrieval", "result": result, "source_turn": state["turn_index"]}
            )
            return result
        if name == "lookup_booking_by_request":
            unknown = state.get("unknown_submission")
            if unknown and str(values.idempotency_key) != unknown["request_key"]:
                raise AgentError("original_request_required", "未知提交只能用原请求标识查证。")
            result = self.booking.lookup_booking_by_request(context, values.idempotency_key)
            if result.data is not None:
                state.update(
                    {
                        "business_result": result.model_dump(mode="json"),
                        "unknown_submission": None,
                        "pending_draft": None,
                        "grant": None,
                        "confirmation_state": "consumed",
                    }
                )
            return result.model_dump(mode="json")
        if name == "confirm_booking":
            draft, grant = state.get("pending_draft"), state.get("grant")
            if not draft or not grant or state.get("confirmation_state") != "authorized":
                raise AgentError(
                    "confirmation_not_authorized", "必须等待合法用户确认入口，模型不能自授权。", 403
                )
            if (
                str(values.draft_id) != draft["draft_id"]
                or values.draft_hash != draft["draft_hash"]
                or str(values.idempotency_key) != draft["suggested_idempotency_key"]
                or grant["revision"] != state["confirmation_revision"]
            ):
                raise AgentError("confirmation_binding_mismatch", "确认授权与当前草稿/版本不一致。")
            if state.get("write_attempted_this_turn"):
                raise AgentError(
                    "write_retry_requires_user", "本轮已尝试提交，先查证原请求，不自动重试。"
                )
            state["write_attempted_this_turn"] = True
            result = self.booking.confirm_booking(
                context, ConfirmBooking(**values.model_dump(), confirmed=True)
            )
            state.update(
                {
                    "business_result": result.model_dump(mode="json"),
                    "pending_draft": None,
                    "grant": None,
                    "confirmation_state": "consumed",
                    "unknown_submission": None,
                }
            )
            return result.model_dump(mode="json")
        if state.get("unknown_submission"):
            raise AgentError("submission_unknown", "原提交状态未知，不能换键或生成新稿。")
        if name == "preview_booking" and state.get("confirmation_state") == "rejected":
            raise AgentError("user_declined", "用户已明确拒绝，不能自动重新生成确认草稿。", 403)
        if name == "preview_booking" and state.get("write_attempted_this_turn"):
            raise AgentError(
                "write_retry_requires_user", "本轮已尝试提交，不能再生成新稿，请先查证原请求。"
            )
        if not state.get("evidence_refs"):
            raise AgentError("rules_required", "先检索当前适用规则，不从常识编造规则。")
        expected = booking_request(state)
        if (
            values.start_at != expected.start_at
            or values.end_at != expected.end_at
            or values.attendees != expected.attendees
        ):
            raise AgentError("request_state_mismatch", "工具参数与有来源的当前需求不一致。")
        if expected.room_code and values.room_code != expected.room_code:
            raise AgentError("request_state_mismatch", "必须遵守用户指定的场地。")
        if name == "query_availability":
            result = self.booking.query_availability(context, values)
            state["candidate_rooms"] = [room.model_dump() for room in result.data.rooms]
            return result.model_dump(mode="json")
        if values.room_code not in {room["room_code"] for room in state.get("candidate_rooms", [])}:
            raise AgentError("candidate_required", "先查询符合当前需求的候选场地。")
        try:
            result = self.booking.preview_booking(context, values)
        except BookingError:
            state.update(
                {
                    "pending_draft": None,
                    "grant": None,
                    "confirmation_state": "idle",
                    "confirmation_revision": state.get("confirmation_revision", 0) + 1,
                }
            )
            raise
        state.update(
            {
                "pending_draft": result.data.model_dump(mode="json"),
                "grant": None,
                "confirmation_state": "pending",
                "confirmation_revision": state.get("confirmation_revision", 0) + 1,
            }
        )
        return result.model_dump(mode="json")
