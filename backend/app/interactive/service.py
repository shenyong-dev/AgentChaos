"""User-owned, version-bound sessions over the existing runtime and booking tools."""

import json
from collections import OrderedDict
from datetime import UTC, datetime
from threading import RLock
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.gateway import ModelGateway
from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.core.deployment import source_fingerprint
from app.db.fixtures import initialize_fixture
from app.interactive.budget import (
    AUTHORIZATION,
    CALL_LIMIT,
    LOCAL_AUTHORIZATION,
    InteractionBudget,
    authorized,
    limits,
    local_unlimited,
)
from app.models import Booking, BookingDraft, TargetVersion
from app.services.booking import utc_now

PREFIX = "ac:t17d:session"


class InteractionService:
    def __init__(self, engine, redis, settings, *, gateway_factory=None, clock=utc_now):
        self.engine, self.redis, self.settings = engine, redis, settings
        self.gateway_factory, self.clock = gateway_factory, clock
        with engine.connect() as connection:
            server, database = connection.execute(text("SELECT @@server_uuid, DATABASE()")).one()
        self.binding = canonical_hash([server, database, "interactive-booking-v1"])
        self.prefix = PREFIX + ":" + self.binding[:24]
        self._runtimes = OrderedDict()
        self._source = source_fingerprint()
        self._runtime_lock = RLock()

    def runtime(self, session_id):
        # Reuse compiled graphs, but never carry them across changed source/Prompt/lock bytes.
        with self._runtime_lock:
            current = source_fingerprint()
            if current != self._source:
                self._runtimes.clear()
                self._source = current
            key = str(session_id)
            if key not in self._runtimes:
                self._runtimes[key] = self._runtime(key)
                if len(self._runtimes) > 64:
                    self._runtimes.popitem(last=False)
            runtime = self._runtimes[key]
            self._runtimes.move_to_end(key)
            if runtime.version_id is None:
                with Session(self.engine) as session:
                    runtime.version_id = session.scalar(
                        select(TargetVersion.id)
                        .where(TargetVersion.config_hash == runtime.manifest["config_hash"])
                        .order_by(TargetVersion.id.desc())
                    )
            return runtime

    def _runtime(self, session_id):
        budget = InteractionBudget(self.redis, self.settings, session_id)
        gateway = (
            self.gateway_factory(str(session_id))
            if self.gateway_factory
            else ModelGateway(
                self.settings,
                budget,
                audit_directory=PROJECT_ROOT / ".local/t17d/model-calls" / str(session_id),
            )
        )
        return AgentRuntime(
            self.engine,
            self.redis,
            self.settings,
            prefix=self.prefix,
            gateway=gateway,
            budget=budget,
            clock=self.clock,
        )

    def access(self):
        runtime = self.runtime("access")
        budget = InteractionBudget(self.redis, self.settings, "access")
        raw = self.redis.hgetall(budget.key) if authorized(self.settings) else {}
        values = {(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in raw.items()}
        available = (
            authorized(self.settings)
            and bool(values)
            and (
                values.get("limit") == -1
                if local_unlimited(self.settings)
                else values.get("limit", 0) > 0
                and values.get("charged_estimate", 0) + values.get("held_upper", 0)
                < values["limit"]
                and values.get("calls_started", 0) < CALL_LIMIT
            )
            and all(
                values.get(k, 0) >= 0 for k in ("charged_estimate", "held_upper", "calls_started")
            )
            and not values.get("protocol_overrun")
            and bool(self.settings.deepseek_api_key.get_secret_value())
        )
        return {
            "version_id": runtime.version_id,
            "environment_id": self.binding[:12],
            "config_hash": runtime.manifest["config_hash"],
            "model_actions_available": bool(available or self.gateway_factory),
            "mode": "offline_control" if self.gateway_factory else "real_provider",
            "reason": "当前源码尚未登记，不可创建会话。"
            if runtime.version_id is None
            else "真实调用需匹配交互授权、密钥和持久账本；读历史不调用模型。"
            if not available and not self.gateway_factory
            else "本地按需调用，无累计次数与金额上限；单次限制与逐次记账保留。"
            if local_unlimited(self.settings)
            else "请求仍按会话/总预算逐次预留。",
            "limits": limits(self.settings),
        }

    def create(self, user_id, body):
        session_id = str(
            uuid5(
                NAMESPACE_URL,
                f"agentchaos:interactive:{self.binding}:{user_id}:{body['request_id']}",
            )
        )
        runtime = self.runtime(session_id)
        if runtime.version_id != body["version_id"]:
            raise AgentError(
                "interactive_version_mismatch", "所选版本与当前源码不匹配，请重新读取环境。", 409
            )
        scope = "test:interactive:" + UUID(session_id).hex
        lock = self.redis.lock(f"{self.prefix}:create:{session_id}", timeout=30, blocking_timeout=0)
        if not lock.acquire(blocking=False):
            raise AgentError("session_busy", "原创建请求正在处理，请按相同标识重试。", 409)
        try:
            key = runtime._key(session_id)
            if self.redis.exists(key):
                return self.view(user_id, session_id)
            with Session(self.engine) as session, session.begin():
                initialize_fixture(session, scope)
            runtime.create(user_id, scope_id=scope, session_id=session_id)
            return self.view(user_id, session_id)
        finally:
            if lock.owned():
                lock.release()

    def view(self, user_id, session_id):
        runtime = self.runtime(session_id)
        result = runtime.view(user_id, session_id, read_previous_version=True)
        metadata, state, _ = runtime.snapshot(user_id, session_id, read_previous_version=True)
        result.update(
            {
                "kind": "booking_agent",
                "read_only": metadata["config_hash"] != runtime.manifest["config_hash"],
                "created_at": metadata["created_at"],
                "environment_id": self.binding[:12],
                "scope_id": metadata["scope_id"],
                "observed_at": datetime.now(UTC).isoformat(),
                "processing": bool(self.redis.exists(runtime._key(session_id) + ":lock"))
                or bool(self.redis.exists(runtime._key(session_id) + ":operation"))
                or result["revision"] != metadata["revision"],
                "mode": "offline_control" if self.gateway_factory else "real_provider",
                "usage": InteractionBudget(self.redis, self.settings, session_id).snapshot(),
                "pending_request": (
                    self.redis.get(runtime._key(session_id) + ":pending") or b""
                ).decode()
                or None,
            }
        )
        result["usage_available"] = True
        if result["read_only"]:
            # Source changes do not migrate a session to today's authorized ledger.
            # Read both historically supported ledgers by the exact owned session ID.
            rows = [
                self.redis.hgetall(f"{namespace}:budget:{authorization}:agent:session:{session_id}")
                for namespace, authorization in (
                    ("ac:t17d", AUTHORIZATION),
                    ("ac:local:interactive", LOCAL_AUTHORIZATION),
                )
            ]
            result["usage"] = {}
            result["usage_available"] = any(rows)
            for row in rows:
                for key, value in row.items():
                    name = key.decode() if isinstance(key, bytes) else key
                    result["usage"][name] = result["usage"].get(name, 0) + int(value)
        # A fresh scoped SQL observation is distinct from checkpoint/model statements.
        try:
            with Session(self.engine) as session:
                bookings = list(
                    session.scalars(
                        select(Booking)
                        .where(
                            Booking.scope_id == metadata["scope_id"],
                            Booking.actor_key == metadata["actor_key"],
                        )
                        .order_by(Booking.id)
                    )
                )
                drafts = list(
                    session.scalars(
                        select(BookingDraft)
                        .where(
                            BookingDraft.scope_id == metadata["scope_id"],
                            BookingDraft.actor_key == metadata["actor_key"],
                            BookingDraft.session_id == str(session_id),
                        )
                        .order_by(BookingDraft.id)
                    )
                )
                result["sql_observation"] = {
                    "available": True,
                    "observed_at": datetime.now(UTC).isoformat(),
                    "booking_count": sum(b.status == "confirmed" for b in bookings),
                    "booking_record_count": len(bookings),
                    "bookings": [
                        {
                            "booking_id": b.booking_uuid,
                            "request_key": b.idempotency_key,
                            "status": b.status,
                            "attendees": b.attendees,
                            "start_at": b.start_at.isoformat(),
                            "end_at": b.end_at.isoformat(),
                        }
                        for b in bookings
                    ],
                    "drafts": [
                        {
                            "draft_id": d.draft_uuid,
                            "status": d.status,
                            "attendees": d.attendees,
                            "expires_at": d.expires_at.isoformat(),
                        }
                        for d in drafts
                    ],
                }
        except Exception:
            result["sql_observation"] = {
                "available": False,
                "booking_count": None,
                "observed_at": datetime.now(UTC).isoformat(),
            }
        unknown = state.get("unknown_submission")
        if unknown and unknown.get("request_key"):
            try:
                result["request_lookup"] = runtime.booking.lookup_booking_by_request(
                    runtime.registry.context(state), UUID(unknown["request_key"])
                ).model_dump(mode="json")
            except Exception:
                result["request_lookup"] = {"status": "unknown", "commit_state": "unknown"}
        return result

    def upgrade(self, user_id, session_id, body):
        """Explicit compatibility adoption keeps old checkpoints and owned bookings."""
        runtime = self.runtime(session_id)
        key = runtime._key(session_id)
        lock = self.redis.lock(key + ":operation", timeout=60, blocking_timeout=0)
        if not lock.acquire(blocking=False):
            raise AgentError("session_busy", "当前会话有请求在处理，请先查回执。")
        try:
            metadata, state, snapshot = runtime.snapshot(
                user_id, session_id, read_previous_version=True
            )
            if metadata["config_hash"] == runtime.manifest["config_hash"]:
                return self.view(user_id, session_id)
            if runtime.version_id is None:
                raise AgentError("agent_version_not_registered", "当前源码版本尚未登记。", 503)
            if metadata["revision"] != body["expected_revision"]:
                raise AgentError("session_revision_conflict", "会话已变化，请重新读取。")
            current_budget = InteractionBudget(self.redis, self.settings, session_id)
            legacy_keys = [
                f"{namespace}:budget:{authorization}:agent:session:{session_id}"
                for namespace, authorization in (
                    ("ac:t17d", AUTHORIZATION),
                    ("ac:local:interactive", LOCAL_AUTHORIZATION),
                )
            ]
            if any(
                key != current_budget.session_key and self.redis.exists(key) for key in legacy_keys
            ):
                raise AgentError(
                    "session_budget_binding_changed",
                    "原会话使用不同预算授权，升级不能转移额度。请保留原记录并新建会话。",
                    409,
                )
            if (
                self.redis.exists(key + ":lock")
                or self.redis.exists(key + ":pending")
                or state.get("unknown_submission")
                or (snapshot.next and not snapshot.interrupts)
            ):
                raise AgentError("session_busy", "原请求或失败节点尚未查清，不能升级会话。")
            # Ownership is checked above. Scope, actor, clock, history and usage are
            # retained; previous metadata/checkpoint remain available for auditing.
            original = {"metadata": metadata, "checkpoint": snapshot.config}
            self.redis.set(key + ":upgrade:" + body["request_id"], json.dumps(original), nx=True)
            runtime.booking.revoke_pending_drafts(runtime.registry.context(state))
            updates = {
                "version_id": runtime.version_id,
                "pending_draft": None,
                "grant": None,
                "confirmation_state": "idle",
                "confirmation_revision": state.get("confirmation_revision", 0) + 1,
                "candidate_rooms": [],
                "last_error": None,
            }
            if snapshot.values:
                runtime.graph.update_state(
                    runtime._config(session_id), {**state, **updates}, as_node="model"
                )
            updated = {
                **metadata,
                "config_hash": runtime.manifest["config_hash"],
                "version_id": runtime.version_id,
                "revision": metadata["revision"] + 1,
                "last_error": None,
            }
            self.redis.set(key, json.dumps(updated, ensure_ascii=False))
        finally:
            if lock.owned():
                lock.release()
        return self.view(user_id, session_id)

    def records(self, user_id, query="", cursor=0):
        """Incremental discovery of existing metadata, confined to this database and owner.

        SCAN is deliberately incremental: next_cursor means the search is not complete.
        Neither browsing nor a source upgrade rewrites checkpoints or sends a model request.
        """
        items = []
        pattern = self.prefix + ":????????-????-????-????-????????????"
        query = query.strip().casefold()
        for _ in range(4):
            cursor, keys = self.redis.scan(cursor=cursor, match=pattern, count=5000)
            for key in keys:
                raw = self.redis.get(key)
                if not raw:
                    continue
                metadata = json.loads(raw)
                if metadata.get("owner_id") != user_id:
                    continue
                session_id = str(UUID(metadata["session_id"]))
                if key.decode() != f"{self.prefix}:{session_id}":
                    continue
                runtime = self.runtime(session_id)
                _, state, _ = runtime.snapshot(user_id, session_id, read_previous_version=True)
                title = state.get("last_user_text") or "未发送消息的会话"
                fields = state.get("fields", {})
                searchable = json.dumps(
                    [session_id, metadata["created_at"], title, fields], ensure_ascii=False
                ).casefold()
                if query and query not in searchable:
                    continue
                observation = self.view(user_id, session_id)
                items.append(
                    {
                        "session_id": session_id,
                        "title": title[:160],
                        "created_at": metadata["created_at"],
                        "version_id": metadata["version_id"],
                        "revision": metadata["revision"],
                        "status": observation["status"],
                        "processing": observation["processing"],
                        "read_only": observation["read_only"],
                        "booking_count": observation["sql_observation"]["booking_count"],
                        "sql_available": observation["sql_observation"]["available"],
                        "fields": {k: v.get("value") for k, v in fields.items()},
                    }
                )
            if not cursor:
                break
        return {
            "items": sorted(items, key=lambda item: item["created_at"], reverse=True),
            "next_cursor": str(cursor) if cursor else None,
            "observed_at": datetime.now(UTC).isoformat(),
        }

    def act(self, user_id, session_id, body, kind):
        runtime = self.runtime(session_id)
        runtime._metadata(
            user_id, session_id
        )  # Deny other owners before checking paid capabilities.
        key = runtime._key(session_id)
        request_id = str(body["request_id"])
        receipt_key = key + ":receipt:" + request_id
        fingerprint = canonical_hash({"kind": kind, **body})
        lock = self.redis.lock(key + ":operation", timeout=300, blocking_timeout=0)
        if not lock.acquire(blocking=False):
            raise AgentError("session_busy", "该会话仍有请求在处理，请先读取原回执。", 409)
        try:
            old = self.redis.get(receipt_key)
            old = json.loads(old) if old else None
            if old and old["payload_hash"] != fingerprint:
                raise AgentError("action_id_conflict", "原请求标识不能绑定不同输入。", 409)
            if old:
                receipt = self.receipt(user_id, session_id, request_id)
                if receipt["status"] == "completed":
                    lock.release()
                    return self.view(user_id, session_id)
                if receipt["status"] == "failed":
                    error = receipt["error"]
                    raise AgentError(error["code"], error["message"], error["status_code"])
                raise AgentError(
                    "interactive_receipt_unknown", "原请求尚未有确定回执，不重新调用模型。", 409
                )
            pending = self.redis.get(key + ":pending")
            if pending:
                pending = pending.decode() if isinstance(pending, bytes) else pending
                previous = self.receipt(user_id, session_id, pending)
                if previous["status"] not in ("completed", "failed"):
                    raise AgentError(
                        "interactive_receipt_unknown",
                        "存在未决请求，请先查证原回执，不插入新输入。",
                        409,
                    )
            if not self.gateway_factory and not self.access()["model_actions_available"]:
                raise AgentError(
                    "interactive_not_authorized", "当前交互调用条件不具备，原会话仍可读取。", 403
                )
            receipt = {
                "request_id": request_id,
                "kind": kind,
                "payload_hash": fingerprint,
                "expected_revision": body["expected_revision"],
                "status": "processing",
                "accepted_at": datetime.now(UTC).isoformat(),
            }
            with self.redis.pipeline() as pipe:
                pipe.set(receipt_key, json.dumps(receipt))
                pipe.set(key + ":pending", request_id)
                pipe.execute()
            try:
                runtime.act(user_id, session_id, body, kind=kind)
                receipt["status"] = "completed"
            except AgentError as exc:
                receipt.update(
                    status="failed",
                    error={
                        "code": exc.code,
                        "message": exc.message,
                        "status_code": exc.status_code,
                    },
                )
                raise
            finally:
                self.redis.set(receipt_key, json.dumps(receipt))
                if receipt["status"] in ("completed", "failed"):
                    self.clear_pending(key, request_id)
        finally:
            if lock.owned():
                lock.release()
        return self.view(user_id, session_id)

    def history(self, user_id, session_id, cursor, limit):
        return self.runtime(session_id).history(
            user_id, session_id, cursor, limit, read_previous_version=True
        )

    def clear_pending(self, key, request_id):
        self.redis.eval(
            "if redis.call('GET',KEYS[1])==ARGV[1] then "
            "return redis.call('DEL',KEYS[1]) end return 0",
            1,
            key + ":pending",
            request_id,
        )

    def receipt(self, user_id, session_id, request_id):
        runtime = self.runtime(session_id)
        runtime._metadata(user_id, session_id, read_previous_version=True)
        key = runtime._key(session_id)
        raw = self.redis.get(key + ":receipt:" + str(request_id))
        receipt = (
            json.loads(raw) if raw else {"request_id": str(request_id), "status": "not_registered"}
        )
        cached = self.redis.get(key + ":action:" + str(request_id))
        if cached:
            cached = json.loads(cached)
            receipt["status"] = "failed" if cached.get("error") else "completed"
            if cached.get("error"):
                receipt["error"] = cached["error"]
        receipt["observed_at"] = datetime.now(UTC).isoformat()
        return receipt

    def trace(self, user_id, session_id, cursor, limit):
        _, state, _ = self.runtime(session_id).snapshot(
            user_id, session_id, read_previous_version=True
        )
        items = state.get("audit", [])
        return {
            "items": items[cursor : cursor + limit],
            "total": len(items),
            "next_cursor": cursor + limit if cursor + limit < len(items) else None,
        }
