"""One bounded provider attempt per ID; durable receipts, no automatic retries."""

import asyncio
import json
import math
import os
import time
from pathlib import Path
from uuid import UUID

import httpx
from dotenv import dotenv_values

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT

ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
PROFILE = {"model": "text-embedding-v4", "dimension": 1024, "endpoint": ENDPOINT}


def unavailable(code, message, status, identifier):
    error = AgentError(code, message, 503)
    error.embedding_record = {"status": status, "request_id": str(identifier), "usage": None}
    return error


def private_path(name):
    path = (PROJECT_ROOT / name).resolve()
    if not path.is_relative_to((PROJECT_ROOT / ".local").resolve()):
        raise ValueError("RAG private path outside .local")
    return path


def write_json(path, value, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())


def normalized(vector, dimensions):
    if not isinstance(vector, list) or len(vector) != dimensions:
        raise ValueError("Embedding dimensions do not match")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
        raise ValueError("Invalid embedding value")
    norm = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("Invalid embedding norm")
    return [v / norm for v in vector]


class EmbeddingClient:
    def __init__(self, credentials_file, audit_root, *, transport=None):
        self.credentials_file = credentials_file
        self.audit_root = Path(audit_root)
        self.transport = transport or self.request

    def request(self, payload, timeout):
        # Only this fixed HTTPS endpoint receives the credential, never model-supplied URLs.
        key = dotenv_values(private_path(self.credentials_file)).get("DASHSCOPE_API_KEY")
        if not key:
            raise ValueError("Embedding credential unavailable")

        async def send():
            async with httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=timeout
            ) as client:
                async with client.stream(
                    "POST",
                    ENDPOINT,
                    json=payload,
                    headers={"Authorization": "Bearer " + key},
                ) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    async for part in response.aiter_bytes():
                        raw.extend(part)
                        if len(raw) > 4_000_000:
                            raise ValueError("Embedding response too large")
                    return json.loads(raw)

        async def bounded():
            return await asyncio.wait_for(send(), timeout)

        return asyncio.run(bounded())

    def embed(self, texts, mode, identifier, deadline):
        if mode not in {"query", "document"} or not 1 <= len(texts) <= 10:
            raise ValueError("Invalid embedding batch")
        if any(not isinstance(t, str) or not t.strip() or len(t.encode()) > 24000 for t in texts):
            raise ValueError("Invalid embedding input")
        remaining = min(20, deadline - time.monotonic())
        if remaining <= 0:
            raise AgentError("embedding_deadline", "本轮检索达到时限，未发送新请求。", 504)
        folder = self.audit_root / str(UUID(str(identifier)))
        folder.mkdir(parents=True, exist_ok=True)
        intent = {"profile": PROFILE, "mode": mode, "input_hash": canonical_hash(texts)}
        path = folder / "intent.json"
        if not path.exists():
            for previous in self.audit_root.glob("*/intent.json"):
                result_path = previous.parent / "result.json"
                error_path = previous.parent / "error.json"
                if result_path.exists():
                    if json.loads(result_path.read_bytes()).get("status") == "completed":
                        continue
                if (
                    error_path.exists()
                    and json.loads(error_path.read_bytes()).get("status") == "failed"
                ):
                    continue
                raise unavailable(
                    "embedding_paused",
                    "有尚未确认的向量请求，已暂停新调用。",
                    "paused",
                    previous.parent.name,
                )
        try:
            write_json(path, intent, exclusive=True)
        except FileExistsError:
            if json.loads(path.read_text(encoding="utf-8")) != intent:
                raise AgentError("embedding_conflict", "原向量请求已绑定其他内容。", 409) from None
            result_path = folder / "result.json"
            if not result_path.exists():
                raise unavailable(
                    "embedding_unknown", "原向量请求结果未知，未重复发送。", "unknown", identifier
                ) from None
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("status") != "completed":
                raise unavailable(
                    "embedding_unknown", "原向量请求未完成，未重复发送。", "unknown", identifier
                ) from None
            if result.get("intent_hash") != canonical_hash(intent):
                raise ValueError("Embedding receipt binding mismatch") from None
            if len(result["vectors"]) != len(texts) or result.get("vectors_hash") != canonical_hash(
                result["vectors"]
            ):
                raise ValueError("Embedding receipt vectors changed") from None
            return [normalized(v, PROFILE["dimension"]) for v in result["vectors"]], result
        try:
            reply = self.transport(
                {
                    "model": PROFILE["model"],
                    "input": {"texts": texts},
                    "parameters": {"text_type": mode, "dimension": PROFILE["dimension"]},
                },
                remaining,
            )
            rows = sorted(reply["output"]["embeddings"], key=lambda row: row["text_index"])
            if [row["text_index"] for row in rows] != list(range(len(texts))):
                raise ValueError("Embedding rows missing or duplicated")
            vectors = [normalized(row["embedding"], PROFILE["dimension"]) for row in rows]
            usage = reply.get("usage")
            if (
                not isinstance(usage, dict)
                or type(usage.get("total_tokens")) is not int
                or usage["total_tokens"] < 0
            ):
                raise ValueError("Embedding usage unavailable")
            result = {
                "status": "completed",
                "intent_hash": canonical_hash(intent),
                "request_id": str(reply.get("request_id", ""))[:120],
                "usage": usage,
                "vectors": vectors,
                "vectors_hash": canonical_hash(vectors),
            }
            write_json(folder / "result.json", result, exclusive=True)
            return vectors, result
        except Exception as exc:
            status = "failed" if isinstance(exc, httpx.HTTPStatusError) else "unknown"
            write_json(
                folder / "error.json",
                {"status": status, "type": type(exc).__name__},
                exclusive=True,
            )
            raise unavailable(
                "embedding_unavailable", "语义检索暂不可用，原请求未自动重发。", status, identifier
            ) from None
