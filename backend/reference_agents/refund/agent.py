"""Reference agent with a verifiable Python interface and injected model/observation ports."""

import json
import re
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from .tools import TOOLS, schemas


class RefundAgent:
    def __init__(self, store, gateway, observer, control, identity, faults=None):
        self.store, self.gateway, self.observer, self.control = store, gateway, observer, control
        self.faults = faults
        self.messages = [
            SystemMessage(
                content=Path(__file__).with_name("prompt.txt").read_text(encoding="utf-8")
            )
        ]
        self.state = {
            **identity,
            "pending_draft": None,
            "grant": None,
            "confirmation_state": "idle",
            "order_id": None,
            "revision": 0,
            "unknown_submission": None,
            "business_result": None,
        }
        self.trace = []

    def _business(self, name, values):
        s = self.state
        if name in ("query_order", "check_refund"):
            order = self.store.order(values.order_id)
            if not order:
                return {
                    "status": "error",
                    "code": "order_not_found",
                    "commit_state": "not_committed",
                }
            return {
                "status": "ok",
                "code": "order_checked",
                "data": order,
                "commit_state": "not_applicable",
            }
        if name == "preview_refund":
            if s.get("unknown_submission"):
                return {
                    "status": "error",
                    "code": "lookup_original_first",
                    "commit_state": "unknown",
                }
            if values.order_id != s.get("order_id") or s["confirmation_state"] == "rejected":
                return {
                    "status": "error",
                    "code": "input_binding_required",
                    "commit_state": "not_committed",
                }
            draft = self.store.preview(values.order_id, s["revision"])
            s.update(pending_draft=draft, grant=None)
            if draft:
                s["confirmation_state"] = "pending"
            return {
                "status": "ok" if draft else "error",
                "code": "confirmation_required" if draft else "not_refundable",
                "data": draft,
                "commit_state": "not_committed",
            }
        if name == "confirm_refund":
            if (
                s.get("unknown_submission")
                and values.idempotency_key != s["unknown_submission"]["request_key"]
            ):
                return {
                    "status": "error",
                    "code": "original_key_required",
                    "commit_state": "unknown",
                }
            row = self.store.confirm(
                values.draft_id, values.draft_hash, values.idempotency_key, s.get("grant")
            )
            if row:
                s.update(
                    business_result=row,
                    grant=None,
                    confirmation_state="submitted",
                    unknown_submission=None,
                )
            return {
                "status": "ok" if row else "error",
                "code": "refund_posted" if row else "confirmation_or_binding_invalid",
                "data": row,
                "commit_state": "committed" if row else "not_committed",
                "request_key": values.idempotency_key,
            }
        row = self.store.lookup(values.idempotency_key)
        if row:
            s.update(business_result=row, unknown_submission=None, confirmation_state="submitted")
        return {
            "status": "ok" if row else "unknown",
            "code": "refund_found" if row else "refund_not_observed",
            "data": row,
            "commit_state": "committed" if row else "unknown",
            "request_key": values.idempotency_key,
        }

    def turn(self, text, *, deadline):
        s = self.state
        order_ids = re.findall(r"O-\d{4}", text)
        declined = any(word in text for word in ("不要退", "拒绝", "不退款", "取消退款"))
        changed = bool(order_ids and order_ids[-1] != s.get("order_id"))
        # A fresh, explicit draft request can reopen a declined order, never authorize it.
        restart = bool(
            s["confirmation_state"] == "rejected"
            and not declined
            and not s.get("unknown_submission")
            and re.search(r"(?:生成|创建|重新申请|重新发起).{0,24}退款|重新.{0,12}退款", text)
        )
        if changed or declined or restart:
            with self.control.transaction():
                self.store.invalidate()
            s.update(
                grant=None,
                pending_draft=None,
                revision=s["revision"] + 1,
                confirmation_state="rejected" if declined else "idle",
            )
            if order_ids:
                s["order_id"] = order_ids[-1]
        draft = s.get("pending_draft")
        if (
            not changed
            and not declined
            and draft
            and text.strip().strip("。！! ") in ("确认", "确认退款", "确认模拟退款")
        ):
            s["grant"] = {k: draft[k] for k in ("draft_id", "draft_hash", "revision")}
            s["confirmation_state"] = "authorized"
            self.observer.emit(
                "confirmation.resumed", draft=draft, revision=s["revision"], decision="confirm"
            )
        else:
            s["grant"] = None
        self.messages.append(HumanMessage(content=text))
        answer = ""
        for _ in range(12):
            self.control.step("refund:model")
            parent, visit = self.observer.node_start("model")
            started = self.observer.emit(
                "model.started",
                node="model",
                parent=parent,
                visit=visit,
                **self.observer.model_metadata,
            )
            prompt = [
                *self.messages,
                SystemMessage(
                    content="可信运行时状态（仅供选择下一工具）："
                    + json.dumps(
                        {
                            k: s[k]
                            for k in (
                                "pending_draft",
                                "grant",
                                "unknown_submission",
                                "confirmation_state",
                            )
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
            try:
                message, record = self.gateway.invoke(prompt, schemas(), deadline=deadline)
            except Exception as exc:
                self.observer.model_end(
                    started,
                    getattr(exc, "agent_record", {"model_requests": 0}),
                    visit=visit,
                    error=getattr(exc, "code", "refund_model_error"),
                )
                raise
            self.observer.model_end(started, record, visit=visit)
            self.observer.emit(
                "node.completed", node="model", parent=parent, visit=visit, status="completed"
            )
            message.id = message.id or str(uuid4())
            self.messages.append(message)
            self.trace.append(
                {
                    "type": "model",
                    "model_returned": record.get("response", {}).get("model_returned"),
                    "call_id": record.get("call_id"),
                }
            )
            calls = message.tool_calls
            if message.invalid_tool_calls:
                # A malformed function call is an attempted tool use, never a final answer.
                for call in [*calls, *message.invalid_tool_calls]:
                    identifier = call.get("id") or str(uuid4())
                    arguments = call.get("args")
                    request = self.observer.emit(
                        "tool.requested",
                        node="tools",
                        tool=call.get("name"),
                        arguments=arguments,
                        tool_call_id=identifier,
                    )
                    self.observer.emit(
                        "tool.error",
                        node="tools",
                        parent=request,
                        tool=call.get("name"),
                        tool_call_id=identifier,
                        arguments=arguments,
                        dispatched=False,
                        visible_result={
                            "status": "error",
                            "code": "tool_contract_rejected",
                            "commit_state": "not_committed",
                        },
                        before=self.store.snapshot(),
                        after=self.store.snapshot(),
                    )
                raise ValueError(
                    "tool_contract_rejected: malformed tool arguments; batch not executed"
                )
            if not calls:
                answer = str(message.content)
                break
            for call in calls:
                self.control.step("refund:tool:" + call["name"])
                before = self.store.snapshot()
                request = self.observer.emit(
                    "tool.requested",
                    node="tools",
                    tool=call["name"],
                    arguments=call["args"],
                    tool_call_id=call["id"],
                )
                capture = {}
                began = time.monotonic()
                grant = deepcopy(s.get("grant"))
                try:
                    if len(calls) != 1 or message.invalid_tool_calls or call["name"] not in TOOLS:
                        raise ValueError("unsupported_tool_batch")
                    values = TOOLS[call["name"]][0].model_validate(call["args"])

                    def business(tool_name=call["name"], arguments=values):
                        # The execution lease lock covers the entire local SQLite commit.
                        with self.control.transaction():
                            return self._business(tool_name, arguments)

                    capture = (
                        self.faults.invoke(
                            s,
                            call["name"],
                            values,
                            business,
                            tool_call_id=call["id"],
                            message_id=message.id,
                            parent=request,
                        )
                        if self.faults
                        else {
                            "raw_result": (raw := business()),
                            "visible_result": raw,
                            "dispatched": True,
                        }
                    )
                    result = capture["visible_result"]
                except (ValidationError, ValueError):
                    result = {
                        "status": "error",
                        "code": "tool_contract_rejected",
                        "commit_state": "not_committed",
                    }
                    capture = {"raw_result": result, "dispatched": False}
                except Exception as exc:
                    self.observer.emit(
                        "tool.error",
                        node="tools",
                        parent=request,
                        tool_call_id=call["id"],
                        tool=call["name"],
                        arguments=call["args"],
                        code="refund_tool_exception",
                        raw_result={"exception_type": type(exc).__name__},
                        before=before,
                        after=self.store.snapshot(),
                    )
                    raise
                fields = result if isinstance(result, dict) else {}
                self.observer.emit(
                    "tool.completed" if fields.get("status") == "ok" else "tool.error",
                    node="tools",
                    parent=request,
                    tool_call_id=call["id"],
                    tool=call["name"],
                    arguments=call["args"],
                    raw_result=capture.get("raw_result"),
                    visible_result=result,
                    dispatched=capture.get("dispatched"),
                    fault_event_id=capture.get("fault_event_id"),
                    before=before,
                    after=self.store.snapshot(),
                    commit_state=fields.get("commit_state"),
                    elapsed_ms=round((time.monotonic() - began) * 1000, 3),
                )
                self.trace.append(
                    {"type": "tool", "name": call["name"], "grant": grant, "result": result}
                )
                if (
                    call["name"] == "preview_refund"
                    and fields.get("code") == "confirmation_required"
                ):
                    self.observer.emit(
                        "confirmation.requested", draft=s["pending_draft"], revision=s["revision"]
                    )
                self.messages.append(
                    ToolMessage(
                        content=json.dumps(result, ensure_ascii=False), tool_call_id=call["id"]
                    )
                )
        else:
            raise RuntimeError("refund_model_step_limit")
        return {
            "assistant_text": answer,
            "status": "waiting_confirmation"
            if s.get("pending_draft")
            and s["confirmation_state"] in ("pending", "authorized", "unknown")
            else "ready",
            "pending_draft": s.get("pending_draft"),
            "trace": self.trace,
            "business_result": s.get("business_result"),
            "confirmation_state": s["confirmation_state"],
        }
