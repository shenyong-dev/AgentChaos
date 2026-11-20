"""Independent dense-vector retrieval of reviewed help; no business retrieval changes."""

import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.copilot.embedding import PROFILE, EmbeddingClient, normalized, private_path
from app.core.config import PROJECT_ROOT
from app.core.roles import permits
from app.guidance.service import catalog

RECIPE = Path(__file__).with_name("knowledge.json")
CONFIG = PROJECT_ROOT / ".local/copilot-rag/runtime.json"


def runtime_config():
    if not CONFIG.exists():
        return None
    if CONFIG.stat().st_size > 16000:
        raise ValueError("RAG configuration too large")
    value = json.loads(CONFIG.read_bytes())
    if value.get("profile") != PROFILE or value.get("authorization") != "COPILOT-RAG-20261116":
        raise ValueError("RAG profile or authorization mismatch")
    return value


def identity():
    value = runtime_config()
    return {
        "recipe_hash": hashlib.sha256(RECIPE.read_bytes()).hexdigest(),
        "runtime_hash": canonical_hash(value),
        "strategy": "dense_semantic_vectors",
    }


def similarity(left, right):
    return sum(a * b for a, b in zip(left, right, strict=True))


class RagIndex:
    def __init__(self, bundle):
        self.bundle = bundle
        if bundle["profile"] != PROFILE or bundle["catalog_hash"] != catalog()[1]:
            raise ValueError("RAG model or platform guide changed")
        if bundle["recipe_hash"] != hashlib.sha256(RECIPE.read_bytes()).hexdigest():
            raise ValueError("RAG knowledge revision changed; rebuild required")
        self.rows = bundle["rows"]
        if not 1 <= len(self.rows) <= 150:
            raise ValueError("RAG index size invalid")
        seen = set()
        for row in self.rows:
            if row["id"] in seen or row["kind"] not in {"knowledge", "scenario"}:
                raise ValueError("Invalid RAG row identity")
            seen.add(row["id"])
            if row["content_hash"] != canonical_hash(row["content"]):
                raise ValueError("RAG content hash mismatch")
            row["vector"] = normalized(row["vector"], PROFILE["dimension"])

    def search(self, vector, role):
        vector = normalized(vector, PROFILE["dimension"])
        # Role filtering happens before scoring; no other owner's records are in this index.
        eligible = [row for row in self.rows if any(permits(role, key) for key in row["roles"])]
        ranked = sorted(
            ((similarity(vector, row["vector"]), row) for row in eligible),
            key=lambda item: (-item[0], item[1]["id"]),
        )
        docs = [
            (score, row) for score, row in ranked if row["kind"] == "knowledge" and score >= 0.35
        ]
        scenes = [
            (score, row) for score, row in ranked if row["kind"] == "scenario" and score >= 0.4
        ]
        return docs[:3], scenes[:2]


class RagRuntime:
    def __init__(self, config=None, *, client=None):
        self.config = runtime_config() if config is None else config
        self.index = None
        self.client = client
        if self.config:
            path = private_path(self.config["bundle_path"])
            if path.stat().st_size > 5_000_000:
                raise ValueError("RAG bundle too large")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != self.config["bundle_sha256"]:
                raise ValueError("RAG vector bundle changed")
            self.index = RagIndex(json.loads(raw))
            self.client = client or EmbeddingClient(
                self.config["credentials_file"], private_path(self.config["audit_root"])
            )

    def retrieve(self, question, tools, identifier, deadline):
        role = tools.observation["role"]
        status = {
            "kind": "retrieval_status",
            "strategy": "semantic_vectors",
            "status": "unavailable",
            "scope": "已核对的平台操作知识",
            "matches": 0,
        }
        if not self.index:
            return [], status, None, None
        if not any(permits(role, key) for row in self.index.rows for key in row["roles"]):
            status["status"] = "no_scope"
            return [], status, None, None
        vectors, receipt = self.client.embed([question], "query", identifier, deadline)
        docs, scenes = self.index.search(vectors[0], role)
        status.update(
            status="matched" if docs else "no_match",
            matches=len(docs),
            bundle_hash=self.config["bundle_sha256"],
        )
        chunks = []
        for score, row in docs:
            content = {
                "kind": "knowledge",
                **row["content"],
                "document_id": row["id"],
                "revision": self.index.bundle["revision"],
                "bundle_hash": self.config["bundle_sha256"],
                "semantic_score": round(score, 4),
                "evidence_kind": "procedure_not_live_state",
            }
            if len(json.dumps(chunks + [content], ensure_ascii=False).encode()) > 4800:
                status["context_truncated"] = True
                break
            chunks.append(content)
        guidance = None
        if scenes:
            first_score, first = scenes[0]
            guidance = {
                "scenario": first["id"],
                "title": first["content"]["title"],
                "ambiguous": len(scenes) > 1 and first_score - scenes[1][0] < 0.025,
                "followups": first["content"]["followups"],
                "requirements": first["content"]["requirements"],
                "action_candidates": [
                    a for a in first["content"]["actions"] if a in tools.allowed_actions
                ],
                "boundary": "场景仅为语义候选；结合当前上下文核对，不证明事实或授予权限。",
            }
        return chunks, status, guidance, {k: v for k, v in receipt.items() if k != "vectors"}


def preload(tools, question, identifier, deadline, factory=None):
    receipt = None
    guidance = None
    try:
        runtime = factory() if factory else RagRuntime()
        chunks, status, guidance, receipt = runtime.retrieve(question, tools, identifier, deadline)
    except (AgentError, ValueError, OSError, KeyError, TypeError) as exc:
        receipt = getattr(
            exc,
            "embedding_record",
            {"status": "unavailable", "request_id": identifier, "usage": None},
        )
        chunks = []
        status = {
            "kind": "retrieval_status",
            "strategy": "semantic_vectors",
            "status": "embedding_unavailable",
            "matches": 0,
            "boundary": "检索不可用不等于没有资料；仍可核对现有对象或阅读基础指南。",
        }
    for chunk in chunks:
        tools.add("search_knowledge", chunk["title"], chunk, limit=4800)
    tools.add("search_knowledge", "本轮知识检索范围", status)
    return guidance, receipt


def query_id(session_id, request_id):
    return str(uuid5(NAMESPACE_URL, "copilot-rag:" + str(session_id) + ":" + str(request_id)))
