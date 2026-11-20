"""Explicit receipt-only reconciliation and cancellation. Never resends an input."""

from copy import deepcopy
from time import monotonic
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.cases.service import CaseService
from app.executors.control import advisory, locked_pair
from app.executors.service import RunService
from app.external.adapter import ExternalAdapter
from app.external.http_port import HttpPort
from app.external.registry import is_external
from app.external.runner import lookup
from app.external.schema import Cancel, HttpEntry
from app.external.transport import Client
from app.models import TestExecution


def selected(engine, settings, owner, run_id, execution_id=None):
    with Session(engine) as session:
        run = RunService.owned_run(session, owner, run_id)
        if not is_external(run.manifest_json.get("adapter_type")):
            return None, []
        user = CaseService.role(session, owner)
        from app.external.self_service import is_self

        if user.role != "user" or not is_self(run.manifest_json["adapter_type"]):
            CaseService.role(session, owner, "operator")
        adapter = ExternalAdapter(engine, settings, run.manifest_json["adapter_type"], owner=owner)
        if adapter.manifest()["config_hash"] != run.manifest_json["target"]["config_hash"]:
            raise AgentError(
                "external_config_changed", "原外部对象来源已变化，请恢复原登记后查证。", 409
            )
        query = select(TestExecution).where(TestExecution.run_id == run.id)
        if execution_id:
            query = query.where(TestExecution.execution_uuid == str(execution_id))
        rows = list(session.scalars(query))
        if execution_id and not rows:
            raise AgentError("execution_not_found", "找不到执行。", 404)
        return adapter, [row.id for row in rows]


def reconcile(engine, settings, owner, run_id, execution_id):
    adapter, rows = selected(engine, settings, owner, run_id, execution_id)
    if adapter is None:
        raise AgentError("external_not_applicable", "此执行不是外部对象。", 409)
    if isinstance(adapter.entry, HttpEntry):
        return readback_reconcile(engine, settings, owner, adapter.entry, rows)
    results = []
    for identifier in rows:
        with advisory(engine, "external-receipt:" + str(identifier)):
            with Session(engine) as session:
                _, execution = locked_pair(session, identifier)
                pending = deepcopy(execution.timing_json.get("external_intents", {}))
                if execution.status not in {"error", "cancelled", "inconclusive"}:
                    raise AgentError(
                        "external_lookup_not_terminal", "请等待执行结束后查证原回执。", 409
                    )
            for key, intent in pending.items():
                if intent["state"] == "received":
                    continue
                try:
                    response = lookup(
                        Client(adapter.entry, settings, owner=owner), intent["request"]
                    )
                    if (
                        intent["request"]["operation"] == "execute"
                        and response["state"] == "working"
                    ):
                        raise AgentError("external_result_unknown", "原外部任务仍在运行。", 409)
                except (AgentError, ValueError):
                    results.append({"request_id": key, "status": "unknown"})
                    continue
                with Session(engine) as session, session.begin():
                    run, execution = locked_pair(session, identifier)
                    timing = deepcopy(execution.timing_json)
                    stored = timing["external_intents"][key]
                    if stored["request_hash"] != intent["request_hash"]:
                        raise AgentError("external_request_conflict", "原请求绑定已变化。", 409)
                    if stored["state"] == "received":
                        if stored["response"] != response:
                            raise AgentError(
                                "external_receipt_conflict", "原请求返回矛盾回执，请复核。", 409
                            )
                    else:
                        stored.update(
                            state="received",
                            response=response,
                            response_hash=canonical_hash(response),
                            late=True,
                        )
                        if intent["request"]["operation"] == "execute":
                            summary = deepcopy(run.summary_json)
                            summary["external"]["held_upper"] -= stored["upper"]
                            summary["external"]["charged_upper"] += stored["upper"]
                            run.summary_json = summary
                        execution.timing_json = timing
                results.append(
                    {
                        "request_id": key,
                        "status": "received",
                        "origin": "external_reported",
                        "response": response,
                    }
                )
    return {"items": results, "execution_status_unchanged": True, "inputs_resent": 0}


def readback_reconcile(engine, settings, owner, entry, rows):
    port = HttpPort(entry, settings, owner)
    results = []
    deadline = monotonic() + 15
    for identifier in rows:
        with advisory(engine, "external-receipt:" + str(identifier)):
            with Session(engine) as session:
                _, execution = locked_pair(session, identifier)
                if execution.status not in {"error", "cancelled", "inconclusive", "failed"}:
                    raise AgentError("external_lookup_not_terminal", "请等待执行结束后查证。", 409)
                pending = deepcopy(execution.timing_json.get("external_intents", {}))
            for key, intent in pending.items():
                if intent["state"] == "received":
                    continue
                binding = intent["request"]["binding"]
                fact = port.readback(key, binding["session_id"], deadline=deadline)
                if fact["status"] != "observed":
                    results.append({"request_id": key, **fact})
                    continue
                with Session(engine) as session, session.begin():
                    _, execution = locked_pair(session, identifier)
                    timing = deepcopy(execution.timing_json)
                    original = timing["external_intents"].get(key)
                    if not original or original["request_hash"] != intent["request_hash"]:
                        raise AgentError("external_request_conflict", "原请求绑定已变化。", 409)
                    receipts = timing.setdefault("external_readback_checks", {}).setdefault(key, [])
                    if fact not in receipts and len(receipts) < 5:
                        receipts.append(fact)
                    execution.timing_json = timing
                results.append({"request_id": key, "status": "fact_observed", "fact": fact})
    return {"items": results, "execution_status_unchanged": True, "inputs_resent": 0}


def cancel_remote(engine, settings, owner, run_id):
    adapter, rows = selected(engine, settings, owner, run_id)
    if adapter is None or isinstance(adapter.entry, HttpEntry):
        return
    deadline = monotonic() + 15
    for identifier in rows:
        if monotonic() >= deadline:
            break
        with advisory(engine, "external-receipt:" + str(identifier)):
            with Session(engine) as session, session.begin():
                _, execution = locked_pair(session, identifier)
                if execution.status != "cancelled":
                    continue
                timing = deepcopy(execution.timing_json)
                originals = list(timing.get("external_intents", {}).values())
                candidates = [i for i in originals if i["request"]["operation"] == "execute"]
                if not candidates:
                    continue
                intent = max(candidates, key=lambda i: i["request"]["data"]["expected_revision"])
                if intent["state"] == "received" and intent["response"]["state"] in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    continue
                original = intent["request"]
                key = str(uuid5(UUID(original["request_id"]), "cancel"))
                if key in timing.get("external_cancellations", {}):
                    continue  # unknown cancellation is not automatically re-sent
                envelope = {
                    **original,
                    "operation": "cancel",
                    "request_id": key,
                    "data": {
                        "original_request_id": original["request_id"],
                        "input_hash": canonical_hash(original["data"]),
                    },
                }
                timing.setdefault("external_cancellations", {})[key] = {
                    "state": "started",
                    "request_hash": canonical_hash(envelope),
                }
                execution.timing_json = timing
            try:
                response = Cancel.model_validate(
                    Client(adapter.entry, settings, owner=owner).call(envelope, deadline=deadline)
                ).model_dump()
                state = "received"
            except (AgentError, ValueError):
                response, state = None, "unknown"
            with Session(engine) as session, session.begin():
                _, execution = locked_pair(session, identifier)
                timing = deepcopy(execution.timing_json)
                timing["external_cancellations"][key].update(state=state, response=response)
                execution.timing_json = timing
