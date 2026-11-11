"""Owned free-form refund sessions over the T18 reference agent, without experimental replay."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langchain_core.messages import messages_from_dict, messages_to_dict
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.adapters.refund import RefundAdapter
from app.agents.errors import AgentError
from app.agents.gateway import ModelGateway
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.core.deployment import source_fingerprint
from app.interactive.budget import (
    CALL_LIMIT,
    InteractionBudget,
    authorized,
    limits,
    local_unlimited,
)
from app.interactive.refund_control import RefundControl, RefundObserver
from app.models import Target, TargetVersion
from reference_agents.refund.agent import RefundAgent


def now():
    return datetime.now(UTC).isoformat()


class RefundInteractionService:
    def __init__(self, engine, redis, settings, *, gateway_factory=None):
        self.engine, self.redis, self.settings = engine, redis, settings
        self.gateway_factory = gateway_factory
        self.adapter = RefundAdapter(engine, settings)
        with engine.connect() as c:
            server, database = c.execute(text("SELECT @@server_uuid,DATABASE()")).one()
        self.binding = canonical_hash([server, database, "interactive-refund-v1"])
        self.prefix = "ac:refund:interactive:" + self.binding[:24]

    def key(self, sid):
        return self.prefix + ":session:" + str(UUID(str(sid)))

    def owned(self, owner, sid):
        raw = self.redis.get(self.key(sid))
        if not raw:
            raise AgentError("refund_session_not_found", "找不到当前账号的退款会话。", 404)
        meta = json.loads(raw)
        if meta.get("owner") != owner or meta.get("binding") != self.binding:
            raise AgentError("refund_session_not_found", "找不到当前账号的退款会话。", 404)
        return meta

    def budget(self, sid):
        return InteractionBudget(
            self.redis,
            self.settings,
            sid,
            directory=PROJECT_ROOT / ".local/refund-interactive/budget-tickets" / str(sid),
        )

    def current(self, owner):
        manifest = self.adapter.manifest()
        with Session(self.engine) as session:
            identifier = session.scalar(
                select(TargetVersion.id)
                .join(Target)
                .where(
                    Target.owner_id == owner,
                    Target.adapter_type == "reference_refund",
                    TargetVersion.config_hash == manifest["config_hash"],
                )
                .order_by(TargetVersion.id.desc())
            )
        return manifest, identifier

    def access(self, owner):
        manifest, version = self.current(owner)
        budget = self.budget("access")
        raw = self.redis.hgetall(budget.key) if authorized(self.settings) else {}
        value = {k.decode(): int(v) for k, v in raw.items()}
        available = bool(
            authorized(self.settings)
            and value
            and not value.get("protocol_overrun")
            and self.settings.deepseek_api_key.get_secret_value()
            and all(
                value.get(k, 0) >= 0 for k in ("charged_estimate", "held_upper", "calls_started")
            )
            and (
                value.get("limit") == -1
                if local_unlimited(self.settings)
                else value.get("limit", 0)
                > value.get("charged_estimate", 0) + value.get("held_upper", 0)
                and value.get("calls_started", 0) < CALL_LIMIT
            )
        )
        return {
            "version_id": version,
            "config_hash": manifest["config_hash"],
            "environment_id": self.binding[:12],
            "limits": limits(self.settings),
            "mode": "offline_control" if self.gateway_factory else "real_provider",
            "model_actions_available": bool(version and (available or self.gateway_factory)),
            "reason": "退款源码尚未登记，请由维护者登记当前版本。"
            if not version
            else "交互模型调用未就绪，请检查工程环境与原日常额度。"
            if not available and not self.gateway_factory
            else "",
        }

    def lock_name(self, sid):
        return "ac:refund:" + canonical_hash([self.binding, str(sid)])[:48]

    @contextmanager
    def locked(self, sid):
        name = self.lock_name(sid)
        with self.engine.connect() as connection:
            acquired = connection.scalar(text("SELECT GET_LOCK(:name,0)"), {"name": name})
            connection.commit()
            if acquired != 1:
                raise AgentError("refund_session_busy", "本会话正在处理，请查原请求回执。", 409)
            try:
                yield connection, name
            finally:
                if connection.in_transaction():
                    connection.rollback()
                connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
                connection.commit()

    def create(self, owner, body):
        sid = str(uuid5(NAMESPACE_URL, f"refund:{self.binding}:{owner}:{body['request_id']}"))
        with self.locked(sid):
            raw = self.redis.get(self.key(sid))
            digest = canonical_hash(body)
            if raw:
                meta = self.owned(owner, sid)
                if meta["creation_hash"] != digest:
                    raise AgentError("refund_request_conflict", "原创建标识内容不一致。", 409)
                return self.view(owner, sid, owned_lock=True)
            manifest, version = self.current(owner)
            if version != body["version_id"]:
                raise AgentError("refund_version_mismatch", "退款版本已变化，请重新读取。", 409)
            scope = "test:refund:interactive:" + UUID(sid).hex
            store = self.adapter.store(scope, owner)
            if store.path.exists():
                raise AgentError(
                    "refund_metadata_missing", "原账本存在但元数据缺失，禁止创建替代状态。", 409
                )
            meta = {
                "session_id": sid,
                "binding": self.binding,
                "owner": owner,
                "scope": scope,
                "version_id": version,
                "config_hash": manifest["config_hash"],
                "source": source_fingerprint(),
                "revision": 0,
                "creation_hash": digest,
                "created_at": now(),
                "status": "creating",
                "history": [],
                "inflight": None,
                "state": None,
                "messages": None,
                "error": None,
            }
            self.redis.set(self.key(sid), json.dumps(meta, ensure_ascii=False), nx=True)
            self.adapter.prepare(scope, owner)
            meta["status"] = "ready"
            self.redis.set(self.key(sid), json.dumps(meta, ensure_ascii=False))
            self.redis.zadd(self.prefix + f":owner:{owner}", {sid: datetime.now(UTC).timestamp()})
        return self.view(owner, sid)

    def active(self, sid):
        with self.engine.connect() as c:
            return (
                c.scalar(text("SELECT IS_USED_LOCK(:name)"), {"name": self.lock_name(sid)})
                is not None
            )

    def view(self, owner, sid, *, owned_lock=False):
        meta = self.owned(owner, sid)
        current, version = self.current(owner)
        state = meta.get("state") or {}
        try:
            ledger = self.adapter.snapshot(meta["scope"], owner)
            store = self.adapter.store(meta["scope"], owner)
            orders = [o for k in ("O-1001", "O-1002") if (o := store.order(k))]
        except Exception:
            ledger, orders = None, None
        processing = bool(meta["inflight"] and not owned_lock and self.active(sid))
        uncertain = bool(meta["inflight"] and not processing)
        read_only = (
            meta["status"] in ("creating", "blocked")
            or uncertain
            or meta["config_hash"] != current["config_hash"]
            or meta["version_id"] != version
        )
        history = meta["history"]
        return {
            "session_id": sid,
            "revision": meta["revision"],
            "version_id": meta["version_id"],
            "config_hash": meta["config_hash"],
            "scope_id": meta["scope"],
            "created_at": meta["created_at"],
            "observed_at": now(),
            "mode": "offline_control" if self.gateway_factory else "real_provider",
            "status": meta["status"],
            "processing": processing,
            "uncertain": uncertain,
            "read_only": read_only,
            "error": meta.get("error"),
            "pending_request": {"request_id": meta["inflight"]["request_id"]}
            if meta["inflight"]
            else None,
            "pending_draft": state.get("pending_draft"),
            "confirmation_state": state.get("confirmation_state", "idle"),
            "messages": history[-100:],
            "history_before": max(0, len(history) - 100) or None,
            "orders": orders,
            "ledger": ledger,
            "usage": self.budget(sid).snapshot(),
            "trace": [json.loads(v) for v in self.redis.lrange(self.key(sid) + ":events", -50, -1)],
        }

    def receipt(self, owner, sid, request_id):
        self.owned(owner, sid)
        raw = self.redis.get(self.key(sid) + ":receipt:" + str(request_id))
        if raw:
            value = json.loads(raw)
        else:
            value = {"status": "unknown", "request_id": str(request_id)}
        if value["status"] == "processing" and not self.active(sid):
            value = {**value, "status": "unknown"}
        return {"receipt": value, "view": self.view(owner, sid)}

    def act(self, owner, sid, body, kind):
        with self.locked(sid) as (connection, lock_name):
            meta = self.owned(owner, sid)
            digest = canonical_hash({"kind": kind, **body})
            receipt_key = self.key(sid) + ":receipt:" + body["request_id"]
            prior = self.redis.get(receipt_key)
            if prior:
                if json.loads(prior)["digest"] != digest:
                    raise AgentError("refund_request_conflict", "同一操作标识内容不一致。", 409)
                if json.loads(prior)["status"] == "processing":
                    raise AgentError(
                        "refund_round_unknown", "原操作未封存，请核对原回执与账本。", 409
                    )
                return self.receipt(owner, sid, body["request_id"])
            if meta["inflight"] or meta["status"] in ("blocked", "creating"):
                raise AgentError(
                    "refund_round_unknown", "原操作未完成，请核对原回执与账本；不自动重放。", 409
                )
            manifest, version = self.current(owner)
            if meta["version_id"] != version or meta["config_hash"] != manifest["config_hash"]:
                raise AgentError(
                    "refund_version_mismatch", "历史会话为只读，请新建当前版本会话。", 409
                )
            if meta["revision"] != body["expected_revision"]:
                raise AgentError("refund_revision_conflict", "会话已更新，请重新核对草稿。", 409)
            if len(json.dumps(meta, ensure_ascii=False).encode()) > 1_000_000:
                raise AgentError("refund_context_limit", "本会话较长，请保留记录并新建会话。", 409)
            draft = (meta.get("state") or {}).get("pending_draft")
            if kind == "decision":
                if (
                    not draft
                    or any(body[k] != draft[k] for k in ("draft_id", "draft_hash"))
                    or (meta.get("state") or {}).get("confirmation_state")
                    not in ("pending", "authorized")
                ):
                    raise AgentError(
                        "refund_draft_mismatch", "草稿已变化，请重新核对完整草稿。", 409
                    )
                user_text = "确认退款" if body["decision"] == "confirm" else "拒绝，不要退款。"
            else:
                user_text = body["text"]
                if (
                    user_text.strip().strip("。！! ") in ("确认", "确认退款", "确认模拟退款")
                    and draft
                ):
                    raise AgentError(
                        "refund_confirmation_required", "请核对完整草稿后使用确认按钮。", 409
                    )
            if not self.access(owner)["model_actions_available"]:
                raise AgentError(
                    "refund_model_unavailable", "交互调用未就绪，未发起模型请求。", 403
                )
            token = str(uuid4())
            meta["inflight"] = {"request_id": body["request_id"], "token": token, "digest": digest}
            self.redis.set(self.key(sid), json.dumps(meta, ensure_ascii=False))
            receipt = {"request_id": body["request_id"], "digest": digest, "status": "processing"}
            self.redis.set(receipt_key, json.dumps(receipt))
            control = RefundControl(
                connection, lock_name, self.redis, self.key(sid), token, meta["source"]
            )
            observer = RefundObserver(
                self.redis, self.key(sid) + ":events", control, manifest, body["request_id"]
            )
            gateway = (
                self.gateway_factory(sid)
                if self.gateway_factory
                else ModelGateway(
                    self.settings,
                    self.budget(sid),
                    control=control,
                    audit_directory=PROJECT_ROOT / ".local/refund-interactive/model-calls" / sid,
                )
            )
            agent = RefundAgent(
                self.adapter.store(meta["scope"], owner),
                gateway,
                observer,
                control,
                {"scope_id": meta["scope"], "session_id": sid, "actor_key": f"user:{owner}"},
            )
            if meta["state"] is not None:
                agent.state = meta["state"]
                agent.messages = messages_from_dict(meta["messages"])
            meta["history"].append(
                {"role": "user", "content": user_text, "request_id": body["request_id"]}
            )
            try:
                observer.emit("input.received", text=user_text, source=kind)
                output = agent.turn(user_text, deadline=control.deadline)
                meta["history"].append(
                    {
                        "role": "assistant",
                        "content": output["assistant_text"],
                        "request_id": body["request_id"],
                    }
                )
                meta["status"], meta["error"] = "ready", None
                receipt.update(status="completed")
            except Exception as exc:
                meta["status"] = "blocked"
                meta["error"] = {
                    "code": exc.code if isinstance(exc, AgentError) else "refund_round_failed",
                    "message": "本轮未完整结束；请核对原回执与模拟账本，不会自动重放。",
                }
                receipt.update(status="failed", error=meta["error"])
            meta.update(
                state=agent.state,
                messages=messages_to_dict(agent.messages),
                revision=meta["revision"] + 1,
                inflight=None,
            )
            receipt["revision"] = meta["revision"]
            # Both metadata and the final receipt commit atomically under the original round token.
            script = """
local m=cjson.decode(redis.call('GET',KEYS[1]) or '{}')
if not m.inflight or m.inflight.token~=ARGV[1] then return 0 end
redis.call('SET',KEYS[1],ARGV[2]);redis.call('SET',KEYS[2],ARGV[3]);return 1
"""
            if (
                self.redis.eval(
                    script,
                    2,
                    self.key(sid),
                    receipt_key,
                    token,
                    json.dumps(meta, ensure_ascii=False),
                    json.dumps(receipt, ensure_ascii=False),
                )
                != 1
            ):
                raise AgentError(
                    "refund_round_unknown", "操作回执未封存，请查原回执，不要重发。", 409
                )
        return self.receipt(owner, sid, body["request_id"])

    def history(self, owner, sid, before=None):
        history = self.owned(owner, sid)["history"]
        end = min(len(history), before if before is not None else len(history))
        start = max(0, end - 100)
        return {"items": history[start:end], "before": start or None}

    def events(self, owner, sid, cursor=0):
        self.owned(owner, sid)
        key = self.key(sid) + ":events"
        items = [json.loads(v) for v in self.redis.lrange(key, cursor, cursor + 99)]
        after = cursor + len(items)
        return {"items": items, "next_cursor": after if after < self.redis.llen(key) else None}

    def records(self, owner, query="", cursor=0):
        key = self.prefix + f":owner:{owner}"
        ids = self.redis.zrevrange(key, cursor, cursor + 99)
        items = []
        consumed = 0
        for raw in ids:
            consumed += 1
            sid = raw.decode()
            meta = self.owned(owner, sid)
            title = next(
                (m["content"][:120] for m in meta["history"] if m["role"] == "user"), "空会话"
            )
            if query and query.lower() not in (sid + title).lower():
                continue
            items.append(
                {
                    "session_id": sid,
                    "title": title,
                    "created_at": meta["created_at"],
                    "status": meta["status"],
                    "version_id": meta["version_id"],
                }
            )
            if len(items) == 20:
                break
        next_cursor = cursor + consumed
        return {
            "items": items,
            "next_cursor": next_cursor if next_cursor < self.redis.zcard(key) else None,
        }
