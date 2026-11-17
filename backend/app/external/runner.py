"""Leased external dialogue port; persistent intent precedes every mutating request."""

import time
from copy import deepcopy
from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.executors.control import locked_pair
from app.external.adapter import ExternalAdapter
from app.external.schema import PROTOCOL, Lookup, Result, Send
from app.external.transport import Client


def request(entry, binding, operation, identifier, data):
    return {
        "protocol": PROTOCOL,
        "revision": entry.card.revision,
        "operation": operation,
        "request_id": str(identifier),
        "binding": binding,
        "data": data,
    }


def validate_result(operation, data, original):
    digest = canonical_hash(original["data"])
    if operation == "prepare":
        if canonical_hash(data) != canonical_hash({"ready": True, "input_hash": digest}):
            raise AgentError("external_receipt_invalid", "外部准备回执不匹配原请求。", 503)
        return data
    try:
        result = Result.model_validate(data)
        limits = original["data"]["limits"]
        if (
            result.input_hash != digest
            or result.revision != original["data"]["expected_revision"] + 1
            or result.usage is not None
            and (
                result.usage.tokens > limits["max_tokens"]
                or result.usage.cost_microusd > limits["max_cost_microusd"]
            )
        ):
            raise ValueError("Receipt or usage mismatch")
        return result.model_dump()
    except ValueError:
        raise AgentError(
            "external_receipt_invalid", "外部回执或用量超出原请求约束。", 503
        ) from None


def lookup(client, original, *, deadline=None):
    value = Lookup.model_validate(
        client.call(
            {
                **original,
                "operation": "lookup",
                "data": {
                    "original_operation": original["operation"],
                    "input_hash": canonical_hash(original["data"]),
                },
            },
            deadline=deadline,
        )
    )
    if (
        value.original_operation != original["operation"]
        or value.input_hash != canonical_hash(original["data"])
        or value.status != "found"
        or value.result is None
    ):
        raise AgentError("external_result_unknown", "尚未查明原调用结果，禁止换键重发。", 409)
    return validate_result(original["operation"], value.result, original)


class Calls:
    def __init__(self, control, entry, settings, recorder=None):
        self.control, self.entry, self.settings, self.recorder = control, entry, settings, recorder
        self.client = Client(entry, settings)

    def reserve(self, original):
        key = original["request_id"]
        with self.control.transaction() as (_, run, execution):
            timing = deepcopy(execution.timing_json)
            intents = timing.setdefault("external_intents", {})
            if key in intents:
                if intents[key]["request"] != original:
                    raise AgentError("external_request_conflict", "原请求标识已绑定其他内容。", 409)
                return {**deepcopy(intents[key]), "_fresh": False}
            if any(i["state"] != "received" for i in intents.values()):
                raise AgentError(
                    "external_pending_unknown", "存在未查明的原调用，禁止发送新输入。", 409
                )
            charge = original["operation"] == "execute"
            upper = self.entry.cost_upper_microusd if charge else 0
            tokens = self.entry.token_upper if charge else 0
            usage = deepcopy(execution.usage_json)
            e = usage.setdefault("external", {"calls_started": 0, "token_upper": 0})
            summary = deepcopy(run.summary_json or {})
            r = summary.setdefault("external", {"charged_upper": 0, "held_upper": 0})
            if charge and (
                e["calls_started"] >= run.manifest_json["limits"]["max_model_calls"]
                or e["token_upper"] + tokens > run.manifest_json["limits"]["max_tokens"]
                or r["charged_upper"] + r["held_upper"] + upper > run.budget_json["agent_microusd"]
            ):
                raise AgentError(
                    "external_budget_exhausted", "外部请求次数、token或声明预算已到上限。", 429
                )
            if charge:
                e["calls_started"] += 1
                e["token_upper"] += tokens
                r["held_upper"] += upper
            intent = {
                "state": "started",
                "request": original,
                "request_hash": canonical_hash(original),
                "upper": upper,
                "attempt": execution.attempt_no,
                "response": None,
            }
            intents[key] = intent
            execution.timing_json, execution.usage_json = timing, usage
            run.summary_json = summary
            return {**deepcopy(intent), "_fresh": True}

    def record(self, original, response=None):
        # A late receipt cannot revive an execution after cancellation or lease loss.
        with Session(self.control.engine) as session, session.begin():
            run, execution = locked_pair(session, self.control.execution_id)
            timing = deepcopy(execution.timing_json)
            intent = timing["external_intents"][original["request_id"]]
            if intent["request_hash"] != canonical_hash(original):
                raise AgentError("external_request_conflict", "原请求绑定已变化。", 409)
            if intent["state"] == "received":
                if response is not None and intent["response"] != response:
                    raise AgentError(
                        "external_receipt_conflict", "同一原请求返回矛盾回执，需人工核对。", 409
                    )
                return
            if response is None:
                intent["state"] = "unknown"
            else:
                intent.update(
                    state="received",
                    response=response,
                    response_hash=canonical_hash(response),
                    late=execution.lease_token != self.control.token,
                )
                if original["operation"] == "execute":
                    summary = deepcopy(run.summary_json)
                    summary["external"]["held_upper"] -= intent["upper"]
                    summary["external"]["charged_upper"] += intent["upper"]
                    run.summary_json = summary
            execution.timing_json = timing

    def call(self, original, *, deadline):
        self.control.step("external." + original["operation"])
        intent = self.reserve(original)
        try:
            if intent["state"] == "received":
                response = intent["response"]
            elif not intent["_fresh"]:
                response = lookup(self.client, original, deadline=deadline)
            else:
                self.control.check()
                response = validate_result(
                    original["operation"], self.client.call(original, deadline=deadline), original
                )
            for pause in (0.25, 0.5, 1, 2, 3):
                if original["operation"] != "execute" or response["state"] != "working":
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(pause, max(0, deadline - time.monotonic())))
                self.control.step("external.lookup")
                response = lookup(self.client, original, deadline=deadline)
            if original["operation"] == "execute" and response["state"] == "working":
                raise AgentError(
                    "external_result_unknown", "外部任务仍在运行，保留原键待查证。", 503
                )
            self.record(original, response)
        except Exception:
            self.record(original)
            raise
        self.control.check()
        if self.recorder:
            self.recorder.emit(
                "state.changed",
                summary={
                    "origin": "external_reported",
                    "operation": original["operation"],
                    "request_id": original["request_id"],
                    "request_hash": canonical_hash(original),
                    "response": response,
                },
            )
        return response


def execute(engine, redis, settings, claim, control, recorder, gateway=None):
    adapter = ExternalAdapter(engine, settings, claim["manifest"]["adapter_type"])
    if adapter.manifest()["config_hash"] != claim["manifest"]["target"]["config_hash"]:
        raise AgentError("target_version_unavailable", "外部对象配置不匹配冻结版本。", 409)
    with engine.connect() as connection:
        server, database = connection.execute(text("SELECT @@server_uuid,DATABASE()")).one()
    binding = {
        "tenant": canonical_hash([server, database, claim["owner_id"], adapter.kind]),
        "scope": claim["scope_id"],
        "session_id": claim["session_id"],
    }
    with control.transaction() as (_, _, execution):
        timing = deepcopy(execution.timing_json)
        previous_output = deepcopy(execution.final_output_json)
        deadline = time.monotonic() + max(
            0,
            (
                datetime.fromisoformat(timing["deadline"])
                - datetime.now(datetime.fromisoformat(timing["deadline"]).tzinfo)
            ).total_seconds(),
        )
    cursor = timing.get("cursor", 0)
    revision = timing.get("external_revision", 0)
    turns = claim["case"]["dsl"]["turns"]
    supplied = timing.get("pending_input")
    if (
        claim["recovery"]
        and previous_output
        and (
            previous_output["status"] in {"failed", "cancelled"}
            or (cursor == len(turns) and not supplied)
        )
    ):
        return previous_output
    calls = Calls(control, adapter.entry, settings, recorder)
    caps = calls.client.call(
        request(
            adapter.entry,
            binding,
            "capabilities",
            uuid5(UUID(claim["session_id"]), "capabilities"),
            {},
        ),
        deadline=deadline,
    )
    if canonical_hash(caps) != canonical_hash(adapter.entry.card.model_dump()):
        raise AgentError(
            "external_capability_changed", "外部能力或版本已变化，未发送测试输入。", 409
        )
    calls.call(
        request(adapter.entry, binding, "prepare", uuid5(UUID(claim["session_id"]), "prepare"), {}),
        deadline=deadline,
    )
    inputs = [(i, t["user"], "script") for i, t in enumerate(turns) if i >= cursor]
    if supplied:
        inputs.append((cursor, supplied["text"], "input"))
    output = None
    for index, message, source in inputs:
        identifier = (
            supplied["request_id"]
            if source == "input"
            else str(uuid5(UUID(claim["session_id"]), f"turn:{index}"))
        )
        payload = Send.model_validate(
            {
                "text": message,
                "expected_revision": revision,
                "limits": {
                    "max_model_calls": 1,
                    "max_tokens": adapter.entry.token_upper,
                    "max_cost_microusd": adapter.entry.cost_upper_microusd,
                    "timeout_seconds": adapter.entry.timeout_seconds,
                },
            }
        ).model_dump()
        recorder.emit(
            "input.received",
            request_id=identifier,
            source=source,
            text=message,
            expected_revision=revision,
        )
        response = calls.call(
            request(adapter.entry, binding, "execute", identifier, payload), deadline=deadline
        )
        revision = response["revision"]
        output = {
            "status": "waiting_confirmation"
            if response["state"] == "waiting_input"
            else response["state"],
            "assistant_text": response["assistant_text"],
            "external": {**response, "origin": "external_reported"},
        }
        with control.transaction() as (_, _, execution):
            current = dict(execution.timing_json)
            cursor += int(source == "script")
            current.update(
                cursor=cursor, external_revision=revision, revision=current.get("revision", 0) + 1
            )
            if source == "input":
                current.pop("pending_input", None)
            execution.timing_json, execution.final_output_json = current, output
        if response["state"] in {"failed", "cancelled"}:
            return output
    if output is None:
        raise AgentError("external_input_missing", "没有可安全继续的外部输入。", 409)
    return output
