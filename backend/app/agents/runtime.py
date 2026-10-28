"""Authenticated interactive sessions over persistent LangGraph checkpoints.

This bounded synchronous prototype is not the T05 persistent Worker executor.
"""

import json
import re
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.budget import AgentBudget
from app.agents.checkpoints import FencedRedisSaver, SessionRedisSaver
from app.agents.errors import AgentError
from app.agents.gateway import ModelGateway
from app.agents.graph import build_agent_graph, looks_like_decline
from app.agents.registry import ToolRegistry
from app.agents.retrieval import KnowledgeRetriever
from app.agents.versions import build_manifest, canonical_hash, register_version
from app.models import TargetVersion
from app.services.booking import BookingService, utc_now


class AgentRuntime:
    def __init__(
        self,
        engine,
        redis,
        settings,
        *,
        prefix="ac:t04:session",
        gateway=None,
        clock=utc_now,
        auto_register=False,
        document_ids=None,
        control=None,
        budget=None,
        recorder=None,
        faults=None,
    ):
        self.engine, self.redis, self.settings = engine, redis, settings
        self.prefix, self.clock = prefix, clock
        self.control = control
        self.booking = BookingService(engine, clock=clock)
        self.retriever = KnowledgeRetriever(engine, document_ids)
        self.registry = ToolRegistry(
            self.booking, self.retriever, control=control, settings=settings, faults=faults
        )
        self.budget = budget or AgentBudget(redis, settings)
        self.gateway = gateway or ModelGateway(settings, self.budget, control=control)
        saver_class = FencedRedisSaver if control else SessionRedisSaver
        self.saver = saver_class(
            **({"control": control} if control else {}),
            redis_client=redis,
            checkpoint_prefix="ac_t04_checkpoints",
            checkpoint_write_prefix="ac_t04_checkpoint_writes",
        )
        self.saver.setup()
        self.graph = build_agent_graph(
            self.gateway, self.registry, self.saver, control=control, recorder=recorder
        )
        self.manifest = build_manifest(settings, self.retriever, self.registry)
        if auto_register:
            self.version_id = register_version(engine, self.manifest)
        else:
            with Session(engine) as session:
                self.version_id = session.scalar(
                    select(TargetVersion.id)
                    .where(TargetVersion.config_hash == self.manifest["config_hash"])
                    .order_by(TargetVersion.id.desc())
                )

    def _key(self, session_id):
        return f"{self.prefix}:{UUID(str(session_id))}"

    @staticmethod
    def _config(session_id, *, with_deadline=False):
        configurable = {"thread_id": str(session_id)}
        if with_deadline:
            configurable["deadline"] = time.monotonic() + 120
        return {"configurable": configurable, "recursion_limit": 64}

    def _metadata(self, user_id, session_id):
        raw = self.redis.get(self._key(session_id))
        metadata = json.loads(raw) if raw else None
        if metadata is None or metadata["owner_id"] != user_id:
            raise AgentError("session_not_found", "当前身份下找不到该会话。", 404)
        if metadata["config_hash"] != self.manifest["config_hash"]:
            raise AgentError("session_version_mismatch", "会话绑定的不可变版本与当前代码不符。")
        return metadata

    def create(self, user_id: int, *, scope_id="demo", reference_clock=None, session_id=None):
        if self.version_id is None:
            raise AgentError("agent_version_not_registered", "当前V0代码尚未登记不可变版本。", 503)
        session_id = session_id or str(uuid4())
        metadata = {
            "session_id": session_id,
            "owner_id": user_id,
            "scope_id": scope_id,
            "actor_key": f"user:{user_id}",
            "reference_clock": (reference_clock or self.clock()).isoformat(),
            "version_id": self.version_id,
            "config_hash": self.manifest["config_hash"],
            "revision": 0,
            "created_at": datetime.now(UTC).isoformat(),
            "last_error": None,
        }
        with self.control.transaction() if self.control else nullcontext():
            self.redis.set(self._key(session_id), json.dumps(metadata, ensure_ascii=False), nx=True)
            actual = self._metadata(user_id, session_id)
            if actual["scope_id"] != scope_id:
                raise AgentError("session_scope_mismatch", "会话与执行数据范围不一致。")
        return self.view(user_id, session_id)

    @staticmethod
    def initial(metadata):
        return {
            **{
                key: metadata[key]
                for key in ("session_id", "actor_key", "scope_id", "reference_clock", "version_id")
            },
            "messages": [],
            "fields": {},
            "turn_index": 0,
            "last_user_text": "",
            "pending_draft": None,
            "confirmation_state": "idle",
            "confirmation_revision": 0,
            "grant": None,
            "denials": [],
            "business_result": None,
            "business_history": [],
            "unknown_submission": None,
            "candidate_rooms": [],
            "retrieval_result": None,
            "evidence_refs": [],
            "audit": [],
            "compactions": 0,
            "last_error": None,
        }

    def snapshot(self, user_id, session_id):
        metadata = self._metadata(user_id, session_id)
        snapshot = self.graph.get_state(self._config(session_id))
        state = snapshot.values or self.initial(metadata)
        return metadata, state, snapshot

    def view(self, user_id, session_id):
        metadata, state, snapshot = self.snapshot(user_id, session_id)
        result = state.get("business_result")
        if result and result.get("request_key"):
            try:
                result = self.booking.lookup_booking_by_request(
                    self.registry.context(state), UUID(result["request_key"])
                ).model_dump(mode="json")
            except Exception:
                result = {
                    "status": "unknown",
                    "commit_state": "unknown",
                    "code": "business_state_unavailable",
                }
        interrupted = bool(snapshot.interrupts)
        assistant = next(
            (
                message.content
                for message in reversed(state["messages"])
                if isinstance(message, AIMessage) and message.content
            ),
            "",
        )
        return {
            "session_id": str(session_id),
            "revision": metadata["revision"],
            "version_id": metadata["version_id"],
            "config_hash": metadata["config_hash"],
            "reference_clock": metadata["reference_clock"],
            "status": "error"
            if metadata.get("last_error")
            else ("waiting_confirmation" if interrupted else "ready"),
            "assistant_text": assistant,
            "fields": state["fields"],
            "confirmation_state": state["confirmation_state"],
            "confirmation_revision": state["confirmation_revision"],
            "pending_draft": state.get("pending_draft"),
            "business_result": result,
            "unknown_submission": state.get("unknown_submission"),
            "denials": state.get("denials", []),
            "evidence_refs": state["evidence_refs"],
            "last_retrieval": state.get("retrieval_result"),
            "compactions": state["compactions"],
            "history_messages": len(state["messages"]),
            "trace": state["audit"][-40:],
            "last_error": metadata.get("last_error"),
            "next_nodes": list(snapshot.next),
        }

    def history(self, user_id, session_id, cursor=0, limit=20):
        _, state, _ = self.snapshot(user_id, session_id)
        messages = [
            message
            for message in state["messages"]
            if isinstance(message, (HumanMessage, AIMessage))
        ]
        selected = messages[cursor : cursor + limit]
        return {
            "items": [
                {"message_id": message.id, "role": message.type, "content": message.content}
                for message in selected
            ],
            "next_cursor": cursor + limit if cursor + limit < len(messages) else None,
        }

    def act(self, user_id, session_id, body: dict, *, kind="message"):
        request_id = str(body["request_id"])
        key = self._key(session_id)
        self._metadata(user_id, session_id)
        lock = self.redis.lock(key + ":lock", timeout=300, blocking_timeout=0, thread_local=False)
        if not lock.acquire(
            blocking=False, **({"token": self.control.token} if self.control else {})
        ):
            raise AgentError("session_busy", "该会话正在处理另一输入，请等待并重新读取版本。")
        try:
            metadata, state, snapshot = self.snapshot(user_id, session_id)
            cache_key = key + ":action:" + request_id
            payload_hash = canonical_hash({"kind": kind, **body})
            cached = self.redis.get(cache_key)
            if cached:
                cached = json.loads(cached)
                if cached["hash"] != payload_hash:
                    raise AgentError("action_id_conflict", "同一输入标识不能用于不同参数。")
                if cached.get("error"):
                    raise AgentError(
                        cached["error"]["code"],
                        cached["error"]["message"],
                        cached["error"]["status_code"],
                    )
                return cached["response"]
            if body["expected_revision"] != metadata["revision"]:
                raise AgentError(
                    "session_revision_conflict", "输入基于旧会话版本，请先读取当前状态。"
                )
            if state["turn_index"] >= 50 and kind != "retry":
                raise AgentError("session_turn_limit", "当前会话达到50轮上限。", 429)
            interrupted = bool(snapshot.interrupts)
            if kind == "retry":
                if not snapshot.next or interrupted or not state["turn_index"]:
                    raise AgentError("nothing_to_retry", "没有待恢复的失败节点。")
                invoke_input = None
            elif kind == "resume":
                if not interrupted or not state.get("pending_draft"):
                    raise AgentError("confirmation_not_pending", "当前没有可恢复的确认中断。")
                draft = state["pending_draft"]
                if (
                    body["draft_id"] != draft["draft_id"]
                    or body["draft_hash"] != draft["draft_hash"]
                    or body["confirmation_revision"] != state["confirmation_revision"]
                ):
                    raise AgentError("confirmation_stale", "确认绑定与当前草稿/中断版本不一致。")
                invoke_input = Command(
                    resume={
                        "kind": body["decision"],
                        "text": "确认当前完整草稿，请提交。"
                        if body["decision"] == "confirm"
                        else "拒绝确认，不要预约。",
                        "draft_id": draft["draft_id"],
                        "draft_hash": draft["draft_hash"],
                        "revision": state["confirmation_revision"],
                        "request_id": request_id,
                    }
                )
            else:
                secret = self.settings.deepseek_api_key.get_secret_value()
                text = body["text"].replace(secret, "[REDACTED]") if secret else body["text"]
                if snapshot.next and not interrupted and metadata.get("last_error"):
                    raise AgentError(
                        "retry_previous_first", "先恢复失败节点，不插入破坏工具消息配对的新输入。"
                    )
                if interrupted:
                    draft = state["pending_draft"]
                    simple = text.strip().strip("。！! ")
                    confirm = simple in ("确认", "确认预约", "确认提交", "确认当前草稿")
                    invoke_input = Command(
                        resume={
                            "kind": "confirm" if confirm else "message",
                            "text": text,
                            "request_id": request_id,
                            "draft_id": draft["draft_id"],
                            "draft_hash": draft["draft_hash"],
                            "revision": state["confirmation_revision"],
                        }
                    )
                else:
                    prior_confirmation = state.get("confirmation_state", "idle")
                    declined = looks_like_decline(text)
                    reopens_booking = bool(
                        re.search("重新预约|继续预约|现在(?:想|要)预约|还是预约|可以预约了", text)
                    )
                    invoke_input = {
                        **(self.initial(metadata) if not snapshot.values else {}),
                        "messages": [HumanMessage(content=text, id=request_id)],
                        "turn_index": state["turn_index"] + 1,
                        "last_user_text": text,
                        "denials": state.get("denials", [])
                        + (
                            [
                                {
                                    "source_turn": state["turn_index"] + 1,
                                    "draft_id": None,
                                    "text": text,
                                }
                            ]
                            if declined
                            else []
                        ),
                        "confirmation_state": "rejected"
                        if declined or (prior_confirmation == "rejected" and not reopens_booking)
                        else "idle",
                    }
            try:
                self.graph.invoke(invoke_input, self._config(session_id, with_deadline=True))
                if not lock.owned():
                    raise AgentError("session_lock_lost", "会话锁已失效，请重新读取状态。", 503)
                metadata.update({"revision": metadata["revision"] + 1, "last_error": None})
                response = self.view(user_id, session_id)
                response["revision"] = metadata["revision"]
                response["last_error"] = None
                response["status"] = "waiting_confirmation" if response["next_nodes"] else "ready"
                with self.control.transaction() if self.control else nullcontext():
                    with self.redis.pipeline() as pipeline:
                        pipeline.set(key, json.dumps(metadata, ensure_ascii=False))
                        pipeline.set(
                            cache_key,
                            json.dumps(
                                {"hash": payload_hash, "response": response}, ensure_ascii=False
                            ),
                        )
                        pipeline.execute()
                return response
            except AgentError as exc:
                metadata.update(
                    {
                        "revision": metadata["revision"] + 1,
                        "last_error": {"code": exc.code, "message": exc.message},
                    }
                )
                with self.control.transaction() if self.control else nullcontext():
                    with self.redis.pipeline() as pipeline:
                        pipeline.set(key, json.dumps(metadata, ensure_ascii=False))
                        pipeline.set(
                            cache_key,
                            json.dumps(
                                {
                                    "hash": payload_hash,
                                    "error": {
                                        "code": exc.code,
                                        "message": exc.message,
                                        "status_code": exc.status_code,
                                    },
                                }
                            ),
                        )
                        pipeline.execute()
                raise
        except RedisError:
            raise AgentError(
                "checkpoint_unavailable", "会话存储暂不可用，不能确认恢复状态。", 503
            ) from None
        finally:
            if lock.owned():
                lock.release()

    def delete_test_session(self, user_id, session_id):
        metadata = self._metadata(user_id, session_id)
        if not metadata["scope_id"].startswith("test:"):
            raise AgentError("session_cleanup_forbidden", "只允许清理本任务明确的测试会话。", 403)
        self.saver.delete_thread(str(session_id))
        key = self._key(session_id)
        keys = list(self.redis.scan_iter(match=key + ":action:*"))
        self.redis.delete(key, *keys)
