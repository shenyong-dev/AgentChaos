"""Deterministic checks over reply, retrieved IDs, OTLP and request-bound facts."""

import hashlib
import json
from pathlib import Path

from app.evaluation.engine import Missing, predicate, subset
from app.evaluation.engine import evaluate as evaluate_reply
from app.external.http_port import MISSING, at
from app.schemas.assertions import ReplyLiterals


def evaluator_manifest():
    sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    from app.evaluation.engine import evaluator_manifest as shared_manifest

    shared = shared_manifest()
    version_hash = hashlib.sha256(json.dumps([sha, shared], sort_keys=True).encode()).hexdigest()
    return {
        "kind": "rule",
        "version": "external-reliability-v1-" + version_hash[:20],
        "source_hash": sha,
        "shared": shared,
        "dsl": "external-reliability-1",
    }


def round_evidence(spec, evidence):
    number = spec.get("turn") or evidence["last_turn"]
    return next((row for row in evidence["rounds"] if row["turn"] == number), {})


def tool_result(spec, observed):
    if not observed or not observed["complete"] or not observed["tools"]:
        raise Missing("缺少这轮可关联的 OpenTelemetry 工具轨迹。")
    tools = observed["tools"]
    position = 0
    missing_arguments = False
    for call in tools:
        step = spec["steps"][position]
        if step["name"] != call["name"]:
            continue
        required, actual = step["arguments"], call["arguments"]
        if required.keys() - actual.keys() or "[REDACTED]" in (actual.get(key) for key in required):
            missing_arguments = True
        elif subset(required, actual):
            position += 1
            if position == len(spec["steps"]):
                break
    passed = position == len(spec["steps"])
    if not passed and missing_arguments:
        raise Missing("工具轨迹缺少用例指定的调用参数。")
    return (
        passed,
        {"trace_id": observed["trace_id"], "tools": tools},
        ("工具调用顺序和指定参数符合预期。" if passed else "观察到的工具顺序或参数与预期不同。"),
    )


def observed_result(spec, evidence):
    if spec["type"] == "external_fact_equals":
        snapshot = evidence["business"][spec["phase"]]
        if snapshot.get("status") != "observed":
            raise Missing(snapshot.get("reason") or "缺少按原请求关联的只读业务快照。")
        value = at(snapshot["data"], spec["path"])
        if value is MISSING or value == "[REDACTED]":
            raise Missing("只读业务快照缺少用例指定的字段。")
        passed = subset(spec["value"], value)
        return (
            passed,
            {"value": value, "request_id": snapshot["request_id"], "origin": "read_only_endpoint"},
            ("只读业务快照与预期一致。" if passed else "只读业务快照与预期不一致。"),
            snapshot.get("event_id"),
        )

    current = round_evidence(spec, evidence)
    if spec["type"] == "external_tool_sequence":
        passed, actual, reason = tool_result(spec, current.get("trace"))
        return passed, actual, reason, current.get("event_id")

    docs = current.get("documents")
    if docs is None:
        raise Missing("缺少这轮有序的检索文档 ID。")
    first = docs[: spec["k"]]
    missing = [item for item in spec["document_ids"] if item not in first]
    return (
        not missing,
        {"document_ids": first, "missing": missing, "origin": current["retrieval_origin"]},
        ("前 K 条文档命中全部预期来源。" if not missing else "前 K 条文档缺少预期来源。"),
        current.get("event_id"),
    )


def result(key, spec, verdict, actual, refs, reason):
    return {
        "key": key,
        "type": spec["type"],
        "verdict": verdict,
        "expected": spec,
        "actual": actual,
        "evidence_refs": refs,
        "reason": reason,
    }


def evaluate(definitions, bundle, *, goal=None):
    if goal is not None:
        raise ValueError("External cases use explicit predicates")
    evidence = (bundle.output or {}).get("external_evidence")
    if evidence is None and all(spec["type"] == "reply_literals" for spec in definitions):
        for spec in definitions:
            ReplyLiterals.model_validate(spec)
        return evaluate_reply(definitions, bundle, guards=False)
    results = evaluate_reply([], bundle, guards=False)["results"]
    if evidence and evidence["transport"]["status"] != "received":
        transport = evidence["transport"]
        refs = ["event:" + transport["event_id"]] if transport.get("event_id") else []
        results.append(
            result(
                "external.transport",
                {"type": "external_receipt", "receipt": "received"},
                "inconclusive",
                transport,
                refs,
                transport["reason"],
            )
        )
    if evidence and "tool_fault_observed" in evidence["transport"]:
        transport = evidence["transport"]
        observed = transport["tool_fault_observed"]
        refs = ["event:" + transport["event_id"]] if transport.get("event_id") else []
        results.append(
            result(
                "external.fault_observed",
                {"type": "external_tool_fault", "observed": True},
                "passed" if observed else "inconclusive",
                {"observed": observed},
                refs,
                "轨迹记录到测试工具失败。" if observed else "缺少可确认工具故障触发的轨迹。",
            )
        )
    for index, spec in enumerate(definitions, 1):
        try:
            if bundle.integrity_errors:
                raise Missing("原始轨迹完整性未通过，无法核对这一项。")
            if spec["type"] == "reply_literals":
                verdict, actual, refs, reason = predicate(spec, bundle)
            elif evidence is None:
                raise Missing("执行缺少本次外部证据。")
            else:
                passed, actual, reason, event_id = observed_result(spec, evidence)
                verdict = "passed" if passed else "failed"
                refs = ["event:" + event_id] if event_id else []
        except Missing as exc:
            verdict, actual, refs, reason = "inconclusive", None, [], str(exc)
        results.append(result(f"case:{index:03}", spec, verdict, actual, refs, reason))
    counts = {
        key: sum(row["verdict"] == key for row in results)
        for key in ("passed", "failed", "inconclusive", "error", "na")
    }
    verdict = next((key for key in ("error", "failed", "inconclusive") if counts[key]), "passed")
    return {"verdict": verdict, "counts": counts, "results": results, "task_outcome": "unknown"}
