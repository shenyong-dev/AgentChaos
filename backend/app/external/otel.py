"""Bounded, run-bound OTLP/HTTP JSON evidence for configured external test services."""

import hashlib
import hmac
import json
import re
import time
from collections import defaultdict
from uuid import UUID, uuid5

from redis.exceptions import RedisError

from app.agents.errors import AgentError
from app.evidence.recorder import redact
from app.external import self_service
from app.external.transport import without_credentials

SPAN_LIMIT = 96
TRACE_LIMIT = 16
BODY_LIMIT = 256 * 1024
TTL = 20 * 60
TRACE = re.compile(r"[0-9a-f]{32}")
SPAN = re.compile(r"[0-9a-f]{16}")
STORE = """
if redis.call('HGET', KEYS[1], 'auth') ~= ARGV[1] then return 0 end
if ARGV[2] == 'incomplete' then
    redis.call('HSET', KEYS[1], 'incomplete', '1')
    return 0
end
local previous = redis.call('HGET', KEYS[1], ARGV[2])
if previous then
    if previous ~= ARGV[3] then redis.call('HSET', KEYS[1], 'incomplete', '1') end
    return 1
end
if redis.call('HLEN', KEYS[1]) >= tonumber(ARGV[4]) then
    redis.call('HSET', KEYS[1], 'incomplete', '1')
    return 0
end
redis.call('HSET', KEYS[1], ARGV[2], ARGV[3])
return 1
"""


def exporter_token(settings, owner, entry_id):
    if not self_service.enabled(settings):
        raise AgentError("external_trace_disabled", "此环境尚未启用外部轨迹接收。", 403)
    return self_service.signature(settings, "external-otlp-v1", owner, entry_id)


def key(settings, trace_id):
    return f"{settings.executor_namespace}:external-otlp:{trace_id}"


def fingerprint(token):
    return hashlib.sha256(token.encode()).hexdigest()


def begin(redis, settings, owner, entry, request_id, argument_names):
    trace_id = uuid5(UUID(request_id), "external-otel-trace").hex
    parent = uuid5(UUID(request_id), "external-otel-parent").hex[:16]
    token = exporter_token(settings, owner, entry.id)
    redis_key = key(settings, trace_id)
    with redis.pipeline(transaction=True) as pipe:
        pipe.hsetnx(redis_key, "auth", fingerprint(token))
        pipe.hsetnx(redis_key, "parent", parent)
        pipe.hsetnx(redis_key, "arguments", json.dumps(sorted(argument_names)))
        pipe.expire(redis_key, TTL)
        pipe.execute()
    return trace_id, f"00-{trace_id}-{parent}-01"


def scalar(value):
    if not isinstance(value, dict):
        return None
    for field in ("stringValue", "boolValue", "doubleValue"):
        if field in value and type(value[field]) in {str, bool, int, float}:
            return value[field]
    if re.fullmatch(r"-?\d{1,19}", str(value.get("intValue", ""))):
        return int(value["intValue"])
    return None


def any_value(value):
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("arrayValue"), dict):
        return [any_value(item) for item in value["arrayValue"].get("values", [])[:64]]
    if isinstance(value.get("kvlistValue"), dict):
        return attributes(value["kvlistValue"].get("values", []))
    return scalar(value)


def attributes(values):
    return {
        item["key"]: any_value(item.get("value"))
        for item in values[:96]
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }


def structured(value):
    if isinstance(value, str):
        if len(value) > 8192:
            return None
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            return None
    return value


def normalize(span, trace_parent, argument_names):
    span_id, parent = span.get("spanId"), span.get("parentSpanId")
    start, end = str(span.get("startTimeUnixNano", "")), str(span.get("endTimeUnixNano", ""))
    if (
        not isinstance(span_id, str)
        or not SPAN.fullmatch(span_id)
        or span_id == "0" * 16
        or not isinstance(parent, str)
        or not SPAN.fullmatch(parent)
        or not re.fullmatch(r"\d{1,20}", start)
        or not re.fullmatch(r"\d{1,20}", end)
        or not 0 < int(start) <= int(end)
    ):
        return None
    attrs = attributes(span.get("attributes", []))
    operation = attrs.get("gen_ai.operation.name")
    result = {
        "id": span_id,
        "parent": parent,
        "start": int(start),
        "anchor": parent == trace_parent
        and (span.get("kind") in (2, "SPAN_KIND_SERVER") or operation == "invoke_agent"),
    }
    tool = attrs.get("gen_ai.tool.name")
    if operation == "execute_tool" and isinstance(tool, str) and 0 < len(tool) <= 120:
        args = structured(attrs.get("gen_ai.tool.call.arguments"))
        status = span.get("status") or {}
        result["tool"] = {
            "name": tool,
            "arguments": {
                name: args[name]
                for name in sorted(argument_names)
                if isinstance(args, dict)
                and name in args
                and (args[name] is None or type(args[name]) in {str, bool, int, float})
                and len(str(args[name])) <= 320
            },
            "status": "error"
            if isinstance(status, dict) and status.get("code") in (2, "STATUS_CODE_ERROR")
            else "observed",
        }
    if operation == "retrieval":
        docs = structured(attrs.get("gen_ai.retrieval.documents"))
        if (
            isinstance(docs, list)
            and len(docs) <= 60
            and all(
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and 0 < len(item["id"]) <= 200
                for item in docs
            )
        ):
            result["documents"] = [item["id"] for item in docs]
    return result


def ingest(redis, settings, token, payload):
    if not isinstance(payload, dict):
        raise ValueError("OTLP trace object required")
    grouped = defaultdict(list)
    count = 0
    for resource in payload.get("resourceSpans", []):
        for scope in resource.get("scopeSpans", []):
            for span in scope.get("spans", []):
                count += 1
                if count > SPAN_LIMIT:
                    raise ValueError("Too many spans")
                trace_id = span.get("traceId")
                if isinstance(trace_id, str) and TRACE.fullmatch(trace_id):
                    grouped[trace_id].append(span)
    if len(grouped) > TRACE_LIMIT:
        raise ValueError("Too many traces")
    digest = fingerprint(token)
    accepted = 0
    for trace_id, spans in grouped.items():
        redis_key = key(settings, trace_id)
        declared, parent, names = redis.hmget(redis_key, "auth", "parent", "arguments")
        if (
            not declared
            or not parent
            or not names
            or not hmac.compare_digest(declared.decode(), digest)
        ):
            continue
        names = json.loads(names)
        for raw in spans:
            item = normalize(raw, parent.decode(), names)
            if item is None:
                redis.eval(STORE, 1, redis_key, digest, "incomplete", "", SPAN_LIMIT + 3)
                continue
            item = redact(without_credentials(item, (token,)), settings)
            accepted += redis.eval(
                STORE,
                1,
                redis_key,
                digest,
                "span:" + item["id"],
                json.dumps(item, ensure_ascii=False, sort_keys=True, allow_nan=False),
                SPAN_LIMIT + 3,
            )
    if count and not accepted:
        raise AgentError("external_trace_unbound", "这批轨迹未绑定有效的测试请求。", 403)
    return {"partialSuccess": {"rejectedSpans": count - accepted}} if count > accepted else {}


def collect(redis, settings, trace_id, deadline, *, wait):
    if trace_id is None:
        return None
    redis_key = key(settings, trace_id)
    end = min(deadline, time.monotonic() + (1.5 if wait else 0))
    previous = ()
    try:
        while True:
            state = redis.hgetall(redis_key)
            spans = [json.loads(v) for k, v in state.items() if k.startswith(b"span:")]
            anchors = [s for s in spans if s["anchor"]]
            observed = tuple(spans)
            if (anchors and observed == previous) or time.monotonic() >= end:
                break
            previous = observed
            time.sleep(max(0, min(0.20, end - time.monotonic())))
    except (RedisError, ValueError):
        return None
    spans.sort(key=lambda row: (row["start"], row["id"]))
    descendants = {s["id"] for s in anchors}
    for _ in range(len(spans)):
        added = {s["id"] for s in spans if s["parent"] in descendants}
        if added <= descendants:
            break
        descendants.update(added)
    valid = [s for s in spans if s["id"] in descendants]
    documents = next((s["documents"] for s in reversed(valid) if "documents" in s), None)
    return {
        "trace_id": trace_id,
        "complete": bool(anchors) and b"incomplete" not in state and len(valid) == len(spans),
        "tools": [s["tool"] for s in valid if "tool" in s],
        "documents": documents,
    }
