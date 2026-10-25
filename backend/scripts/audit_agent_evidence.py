"""Audit preserved real calls, pairing and sanitized artifacts; sends no model requests."""

import hashlib
import json
import shutil
from datetime import UTC, datetime

from langchain_core.messages import AIMessageChunk

from app.core.config import PROJECT_ROOT, Settings


def artifact_digest(path):
    payload = path.read_bytes()
    if path.suffix.lower() in {".json", ".xml", ".mmd", ".txt", ".md", ".csv", ".html"}:
        # Match repository text after Git's eol=lf normalization on Windows.
        payload = payload.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode()
    return hashlib.sha256(payload).hexdigest()


def main():
    root = PROJECT_ROOT / "docs/evidence/T04"
    original = PROJECT_ROOT / ".local/t04/model-calls"
    destination = root / "protocol-and-initial-calls"
    destination.mkdir(exist_ok=True)
    for path in original.glob("*.json"):
        shutil.copy2(path, destination / path.name)
    records = {}
    secret = Settings().deepseek_api_key.get_secret_value()
    for path in root.rglob("*.json"):
        raw = path.read_text(encoding="utf-8")
        assert not secret or secret not in raw, path
        value = json.loads(raw)
        if "call_id" in value and "request" in value and "chunks" in value:
            records[value["call_id"]] = (path, value)
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    successes, errors, malformed = 0, [], []
    pairing_checks, streaming_checks = 0, 0
    for path, record in records.values():
        assert record["role"] == "agent" and record["model_requests"] == 1
        assert record["thinking"] == "disabled"
        pending = set()
        for message in record["request"]["messages"]:
            data = message["data"]
            if message["type"] == "ai":
                assert not pending, path
                pending = {
                    call["id"]
                    for call in data.get("tool_calls", []) + data.get("invalid_tool_calls", [])
                }
            if message["type"] == "tool":
                assert data["tool_call_id"] in pending, path
                pending.remove(data["tool_call_id"])
                pairing_checks += 1
        assert not pending, path
        if record["status"] != "completed":
            errors.append(
                {
                    "call_id": record["call_id"],
                    "code": record["error_code"],
                    "usage": record.get("usage"),
                    "reservation_microusd": record["reservation_microusd"],
                }
            )
            continue
        combined = None
        for chunk in record["chunks"]:
            current = AIMessageChunk(
                content=chunk["content"],
                tool_call_chunks=chunk["tool_call_chunks"],
                usage_metadata=chunk["usage"],
            )
            combined = current if combined is None else combined + current
        assert combined.content == record["response"]["content"], path
        assert combined.tool_calls == record["response"]["tool_calls"], path
        assert combined.invalid_tool_calls == record["response"]["invalid_tool_calls"], path
        assert combined.usage_metadata == record["usage"] and record["usage"], path
        if record["response"]["invalid_tool_calls"]:
            malformed.append(
                {
                    "call_id": record["call_id"],
                    "count": len(record["response"]["invalid_tool_calls"]),
                }
            )
        streaming_checks += 1
        successes += 1
        for key in usage:
            usage[key] += record["usage"][key]
    files = {
        path.relative_to(root).as_posix(): artifact_digest(path)
        for path in root.rglob("*")
        if path.is_file() and path.name != "call-audit.json"
    }
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "network_calls_during_audit": 0,
        "real_request_attempts_preserved": len(records),
        "completed_with_usage": successes,
        "failed_or_unknown": errors,
        "responses_with_malformed_tool_json": malformed,
        "stream_and_final_matches": streaming_checks,
        "tool_message_pairings_in_requests": pairing_checks,
        "usage_sum": usage,
        "exact_local_model_key_occurrences": 0,
        "evidence_hash_method": "SHA256; UTF-8 text normalized to LF, binary files unchanged",
        "evidence_file_sha256": files,
    }
    (root / "call-audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "evidence_file_sha256"},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
