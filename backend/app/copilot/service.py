"""Bounded model/tool loop, immutable per-request receipts and verified source IDs."""

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.gateway import ModelGateway
from app.agents.versions import canonical_hash
from app.cases.service import CaseService
from app.copilot.budget import (
    CALL_LIMIT,
    SESSION_CALL_LIMIT,
    CopilotBudget,
    authorized,
    unlimited_calls,
)
from app.copilot.sources import SourceTools, clean
from app.core.cloud import authorized as cloud_authorized
from app.core.config import PROJECT_ROOT
from app.core.deployment import source_fingerprint
from app.schemas.copilot import CitedStatement, CopilotAnswer, CopilotContext

TTL = 8 * 3600


def configuration(settings):
    files = list(Path(__file__).parent.glob("*.py")) + [Path(__file__).with_name("prompt.txt")]
    files += [
        PROJECT_ROOT / name
        for name in (
            "backend/app/agents/gateway.py",
            "backend/app/schemas/copilot.py",
            "backend/app/guidance/service.py",
            "backend/app/guidance/catalog.json",
            "backend/uv.lock",
        )
    ]
    value = {
        "role": "copilot",
        "model": settings.copilot_model,
        "thinking": settings.copilot_thinking,
        "max_output_tokens": settings.copilot_max_output_tokens,
        "protocol": "copilot-1",
        "sources": {
            p.relative_to(PROJECT_ROOT).as_posix(): canonical_hash(p.read_text(encoding="utf-8"))
            for p in files
        },
        "request_seconds": min(45, settings.model_timeout_seconds),
        "backend_source_fingerprint": source_fingerprint(),
        "max_turn_calls": 3,
        "local_unlimited_calls": unlimited_calls(settings),
        "context_bytes": 32768,
        "cloud_binding": [settings.cloud_deployment_id, settings.cloud_approval_sha256],
    }
    return {**value, "config_hash": canonical_hash(value)}


class CopilotService:
    def __init__(self, engine, redis, settings, *, tools_factory=None, gateway_factory=None):
        self.engine, self.redis, self.settings = engine, redis, settings
        self.tools_factory = tools_factory or (
            lambda owner, context: SourceTools(engine, settings, owner, context)
        )
        self.gateway_factory = gateway_factory
        self.prefix = (
            "ac:t17e:copilot:"
            + canonical_hash([str(engine.url.set(password=None)), settings.executor_namespace])[:16]
        )

    def key(self, session_id):
        return self.prefix + ":session:" + str(UUID(str(session_id)))

    def identity(self, owner):
        from app.auth.service import account_stamp

        with Session(self.engine) as session:
            return account_stamp(CaseService.role(session, owner))

    def access(self, owner):
        self.identity(owner)
        model = configuration(self.settings)
        ledger = CopilotBudget(self.redis, self.settings, "access")
        raw = self.redis.hgetall(ledger.key)
        account = {(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in raw.items()}
        cloud = cloud_authorized(self.settings, "copilot")
        ledger_ready = (
            account.get("limit") == -1
            if cloud
            else account.get("limit", 0) > 0
            and account.get("held_upper", 0) + account.get("charged_estimate", 0)
            < min(account.get("limit", 0), int(self.settings.copilot_budget_usd * 1e6))
        )
        enabled = (
            authorized(self.settings)
            and bool(self.settings.deepseek_api_key.get_secret_value())
            and ledger_ready
            and (unlimited_calls(self.settings) or account.get("calls_started", 0) < CALL_LIMIT)
            and all(
                account.get(k, 0) >= 0
                for k in ("held_upper", "charged_estimate", "calls_started", "token_upper")
            )
            and not account.get("protocol_overrun")
        )
        return {
            "enabled": bool(enabled),
            "reason": "" if enabled else "AI调用未启用或独立额度不可用；基础指引仍可阅读。",
            "configuration": model,
            "budget": account,
            "limits": {
                "turn_calls": 3,
                "session_calls": None if unlimited_calls(self.settings) else SESSION_CALL_LIMIT,
                "total_calls": None if unlimited_calls(self.settings) else CALL_LIMIT,
                "session_microusd": None if cloud else 100000,
                "request_seconds": 45,
                "turn_seconds": 120,
            },
        }

    def create(self, owner, binding, values):
        context = CopilotContext.model_validate(values.model_dump(exclude={"request_id"}))
        tools = self.tools_factory(owner, context)
        config = configuration(self.settings)
        session_id = str(
            uuid5(NAMESPACE_URL, f"{self.prefix}:{owner}:{binding}:{values.request_id}")
        )
        context_data = context.model_dump(mode="json")
        record = {
            "session_id": session_id,
            "owner": owner,
            "auth_binding": binding,
            "identity_stamp": self.identity(owner),
            "context": context_data,
            "context_hash": canonical_hash(context_data),
            "configuration_hash": config["config_hash"],
            "created_at": datetime.now(UTC).isoformat(),
            "selected": self.selection_metadata(tools.observation["selection"]),
            "turns": 0,
        }
        key = self.key(session_id)
        self.redis.set(key, json.dumps(record, ensure_ascii=False), nx=True, ex=TTL)
        existing = self.load(owner, binding, session_id)
        if existing["context_hash"] != record["context_hash"]:
            raise AgentError(
                "copilot_create_conflict", "原创建请求已绑定其他上下文，请重新打开助手。", 409
            )
        return self.public(existing)

    def load(self, owner, binding, session_id):
        raw = self.redis.get(self.key(session_id))
        if not raw:
            raise AgentError(
                "copilot_session_missing", "助手会话已过期，请在当前页面重新开始。", 404
            )
        record = json.loads(raw)
        if (
            record["owner"] != owner
            or record["auth_binding"] != binding
            or record["identity_stamp"] != self.identity(owner)
        ):
            raise AgentError("copilot_session_forbidden", "当前身份不能访问此助手会话。", 403)
        if record["configuration_hash"] != configuration(self.settings)["config_hash"]:
            raise AgentError(
                "copilot_version_changed", "助手版本已变化，请在当前上下文重新开始。", 409
            )
        self.tools_factory(owner, CopilotContext.model_validate(record["context"]))
        return record

    def public(self, record):
        return {
            k: record[k]
            for k in (
                "session_id",
                "context",
                "context_hash",
                "configuration_hash",
                "created_at",
                "selected",
                "turns",
            )
        }

    def selection_metadata(self, selected):
        if not selected:
            return None
        return clean(
            {key: str(selected[key])[:160] for key in ("kind", "id", "title", "status")},
            self.settings,
        )

    def turn_key(self, session_id, request_id):
        return self.key(session_id) + ":turn:" + str(UUID(str(request_id)))

    def read_turn(self, owner, binding, session_id, request_id):
        self.load(owner, binding, session_id)
        raw = self.redis.get(self.turn_key(session_id, request_id))
        if not raw:
            raise AgentError("copilot_turn_missing", "没有该问题的回执，请保留原请求标识。", 404)
        return json.loads(raw)

    def ask(self, owner, binding, session_id, body, *, check=None):
        record = self.load(owner, binding, session_id)
        if body.context_hash != record["context_hash"]:
            raise AgentError("copilot_context_changed", "当前页面或对象已变化，请重新开始。", 409)
        question = clean(body.question.strip(), self.settings)
        if not question:
            raise AgentError("copilot_question_empty", "请输入要解释的问题。", 400)
        key = self.turn_key(session_id, body.request_id)
        question_hash = canonical_hash(question)
        lock = self.redis.lock(self.key(session_id) + ":lock", timeout=150, blocking_timeout=0)
        if not lock.acquire(blocking=False):
            raise AgentError("copilot_busy", "本会话上一问题仍在处理，请查询原回执。", 409)
        try:
            record = self.load(owner, binding, session_id)
            old = self.redis.get(key)
            if old:
                old = json.loads(old)
                if old["question_hash"] != question_hash:
                    raise AgentError("copilot_request_conflict", "同一请求标识不能更换问题。", 409)
                return old
            if not unlimited_calls(self.settings) and record["turns"] >= SESSION_CALL_LIMIT:
                raise AgentError(
                    "copilot_session_limit", "本会话已达到12轮上限，请查看已有记录。", 429
                )
            tools = self.tools_factory(owner, CopilotContext.model_validate(record["context"]))
            tools.invoke("read_guide", {})
            if record["context"].get("object_kind") == "case":
                tools.invoke("read_selection", {})
            result = {
                "request_id": str(body.request_id),
                "session_id": str(session_id),
                "context_hash": record["context_hash"],
                "question": question,
                "question_hash": question_hash,
                "status": "processing",
                "mode": "offline_control" if self.gateway_factory else "ai",
                "created_at": datetime.now(UTC).isoformat(),
                "calls": [],
                "sources": [],
                "trace": [],
                "answer": None,
                "error": None,
            }
            self.redis.set(key, json.dumps(result, ensure_ascii=False), nx=True, ex=TTL)
            try:
                if record["context"]["page"] in ("judging", "semantic"):
                    result["mode"] = "deterministic"
                    result["status"] = "restricted"
                    result["error"] = (
                        "独立标注与语义复核页面仅提供通用指南，不将问题或样本发送模型。"
                    )
                elif not self.gateway_factory and not self.access(owner)["enabled"]:
                    result["mode"] = "deterministic"
                    result["status"] = "unavailable"
                    result["error"] = "AI未启用；以下为版本化基础指南，未调用模型。"
                else:
                    self.run(owner, binding, record, body, question, tools, result, check)
            except (AgentError, ValidationError, ValueError, TypeError, KeyError) as exc:
                result["status"] = "failed"
                result["answer"] = None
                result["error"] = getattr(
                    exc, "message", "返回内容或来源校验未通过，未展示为可靠结论。"
                )
                result["error_code"] = getattr(exc, "code", "copilot_answer_invalid")
            except Exception:
                result["status"] = "failed"
                result["answer"] = None
                result["error"] = "助手请求未完成，请查看原回执；基础指引仍可使用。"
                result["error_code"] = "copilot_unavailable"
            result["sources"] = list(tools.sources.values())
            result["trace"] = tools.trace
            result["finished_at"] = datetime.now(UTC).isoformat()
            result["semantic_boundary"] = "来源ID、归属和快照完整性已校验；语义支持仍需人工核对。"
            # Retain the receipt on logout/version change; never deliver it to a new identity.
            self.redis.set(
                key, json.dumps(clean(result, self.settings), ensure_ascii=False), ex=TTL
            )
            record["turns"] += 1
            updated = self.redis.set(
                self.key(session_id), json.dumps(record, ensure_ascii=False), xx=True, keepttl=True
            )
            if not updated:
                raise AgentError("copilot_session_expired", "助手会话已过期，原问题回执保留。", 409)
            if check:
                check()
            self.load(owner, binding, session_id)
            return result
        finally:
            if lock.owned():
                lock.release()

    def run(self, owner, binding, record, body, question, tools, result, check):
        start = time.monotonic()
        budget = CopilotBudget(self.redis, self.settings, record["session_id"])
        gateway = (
            self.gateway_factory(budget)
            if self.gateway_factory
            else ModelGateway(
                self.settings,
                budget,
                role="copilot",
                audit_directory=PROJECT_ROOT / ".local/t17e/model-calls" / record["session_id"],
            )
        )
        messages = [
            SystemMessage(Path(__file__).with_name("prompt.txt").read_text(encoding="utf-8")),
            HumanMessage(
                json.dumps(
                    {
                        "question": question,
                        "page": record["context"]["page"],
                        "selection": self.selection_metadata(tools.observation["selection"]),
                        "allowed_action_ids": tools.allowed_actions,
                        "navigation": tools.navigation(),
                        "source": list(tools.sources.values()),
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        # Small discussion memory belongs to this exact object/session; it is not evidence.
        history = self.redis.lrange(self.key(record["session_id"]) + ":history", -2, -1)
        if history:
            messages.insert(
                1,
                HumanMessage(
                    "同一对象的历史讨论，仅作语境，不是证据；事实须用本轮工具重新取得："
                    + json.dumps([json.loads(x) for x in history], ensure_ascii=False)
                ),
            )
        seen_calls = set()
        for step in range(3):
            if check:
                check()
            self.load(owner, binding, record["session_id"])
            if time.monotonic() - start >= 120:
                raise AgentError("copilot_deadline", "助手达到本轮时限，请查看已取得的来源。", 504)
            functions = tools.schemas() if step < 2 else []
            try:
                message, call = gateway.invoke(messages, functions, deadline=start + 120)
            except AgentError as exc:
                call = getattr(exc, "agent_record", None)
                if call:
                    result["calls"].append(
                        {
                            k: call.get(k)
                            for k in (
                                "call_id",
                                "role",
                                "model_requested",
                                "usage",
                                "estimated_peak_microusd",
                                "status",
                                "error_code",
                                "elapsed_ms",
                            )
                        }
                    )
                raise
            result["calls"].append(
                {
                    k: call.get(k)
                    for k in (
                        "call_id",
                        "role",
                        "model_requested",
                        "usage",
                        "estimated_peak_microusd",
                        "status",
                        "elapsed_ms",
                    )
                }
            )
            if check:
                check()
            if message.invalid_tool_calls:
                raise AgentError("copilot_tool_invalid", "模型工具参数无效，未执行。", 400)
            if message.tool_calls:
                if step == 2 or len(message.tool_calls) > 3:
                    raise AgentError("copilot_tool_limit", "助手超出只读取证步数或工具上限。", 400)
                messages.append(message)
                for tool in message.tool_calls:
                    if not tool.get("id") or tool["id"] in seen_calls:
                        raise AgentError("copilot_tool_invalid", "工具调用标识缺失或重复。", 400)
                    seen_calls.add(tool["id"])
                    source = tools.invoke(tool["name"], tool["args"])
                    messages.append(
                        ToolMessage(json.dumps(source, ensure_ascii=False), tool_call_id=tool["id"])
                    )
                continue
            answer = CopilotAnswer.model_validate_json(message.content)
            cited_missing = [
                line for line in answer.missing_evidence if isinstance(line, CitedStatement)
            ]
            for group in (answer.facts, answer.possible_causes, answer.next_steps, cited_missing):
                for statement in group:
                    if any(ref not in tools.sources for ref in statement.citations):
                        raise AgentError(
                            "copilot_reference_invalid",
                            "答案引用不存在或不属于本轮来源，已撤回。",
                            409,
                        )
            if not set(answer.action_ids).issubset(tools.allowed_actions):
                raise AgentError(
                    "copilot_action_invalid", "答案动作不符合当前导航权限，已撤回。", 403
                )
            if any(
                datetime.fromisoformat(source["expires_at"]) <= datetime.now(UTC)
                for source in tools.sources.values()
            ):
                raise AgentError(
                    "copilot_sources_expired", "取证后状态已过期，请重新核对后再问。", 409
                )
            # Keep control IDs in action_ids; prose uses the same verified button names.
            for group in (answer.facts, answer.possible_causes, answer.next_steps, cited_missing):
                for statement in group:
                    for action in tools.navigation():
                        statement.text = re.sub(
                            r"(?<![\w.])" + re.escape(action["id"]) + r"(?![\w.])",
                            lambda _, label=action["label"]: label,
                            statement.text,
                        )
            result.update(
                {"status": "completed", "answer": clean(answer.model_dump(), self.settings)}
            )
            history_key = self.key(record["session_id"]) + ":history"
            memory = {
                "question": question[:500],
                "discussion_summary": [
                    clean(line.text[:220], self.settings) for line in answer.facts[:2]
                ],
            }
            self.redis.rpush(history_key, json.dumps(memory, ensure_ascii=False))
            self.redis.ltrim(history_key, -2, -1)
            self.redis.expire(history_key, TTL)
            return
        raise AgentError("copilot_tool_limit", "本轮取证结束，未取得有效最终回答。", 503)
