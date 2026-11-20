"""Readiness is rechecked on commit; only the original run service writes runs."""

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.deployment import database_binding, execution_fingerprint, source_fingerprint
from app.core.model_access import require_model_call
from app.executors.service import RunService, frozen_case
from app.external.registry import entry_for, is_external
from app.models import TargetVersion, TestCase, TestRun
from app.schemas.runs import CreateRun
from app.schemas.workflow import RunPlan
from app.services.readiness import collect_readiness
from app.workbench.service import WorkbenchService


def normalized(values):
    result = values.model_dump(mode="json")
    result.pop("request_id", None)
    if result.get("fault_profile_id") is None:
        result.pop("fault_profile_id", None)
    if result.get("external_fault") is None:
        result.pop("external_fault", None)
    return result


def differences(before, after, prefix=""):
    changes = []
    for key in sorted(set(before) | set(after)):
        a, b = before.get(key), after.get(key)
        field = prefix + key
        if isinstance(a, dict) and isinstance(b, dict):
            changes += differences(a, b, field + ".")
        elif a != b:
            changes.append({"field": field, "before": a, "after": b})
    return changes


class WorkflowService:
    def __init__(self, engine, settings, *, readiness=collect_readiness, mode="real_provider"):
        self.engine, self.settings, self.readiness, self.mode = engine, settings, readiness, mode
        if mode != "real_provider" and not (
            mode == "offline_test_double"
            and settings.mysql_database.startswith("agentchaos_t17h_verify_")
            and not settings.deepseek_api_key.get_secret_value()
            and not any(
                (
                    settings.allow_paid_model_calls,
                    settings.executor_allow_paid_model_calls,
                    settings.casegen_allow_paid_model_calls,
                    settings.judge_allow_paid_model_calls,
                    settings.external_agent_calls_enabled,
                )
            )
        ):
            raise AgentError(
                "workflow_control_not_allowed",
                "离线控制只允许无密钥、无付费的本项隔离验证库。",
                403,
            )

    def client(self):
        return Redis.from_url(self.settings.redis_url, socket_timeout=3, socket_connect_timeout=3)

    def key(self, owner, plan):
        return f"{self.settings.executor_namespace}:workflow:{owner}:{plan}"

    def environment(self):
        return canonical_hash(
            {
                "source": source_fingerprint(),
                "database": database_binding(self.settings),
                "execution": execution_fingerprint(self.settings),
                "mode": self.mode,
            }
        )

    def source(self, owner, identifier):
        workbench = WorkbenchService(self.engine, self.settings)
        with Session(self.engine) as session:
            workbench._identity(session, owner)
        value = RunService(self.engine, self.settings).read(owner, identifier)
        manifest = value["manifest"]
        with Session(self.engine) as session:
            target_id = session.get(TargetVersion, manifest["target_version_id"]).target_id
        cases = manifest.get("cases", [])
        if not cases or any(c.get("content", {}).get("dataset_split") != "dev" for c in cases):
            raise AgentError(
                "workflow_development_only", "正式保留/校准运行不能复制到开发流程。", 403
            )
        configuration = {
            "request_id": str(uuid4()),
            "target_version_id": manifest["target_version_id"],
            "case_revision_ids": sorted(c["case_revision_id"] for c in cases),
            "repetitions": manifest["repetitions"],
            "seed": manifest["seed"],
            "limits": manifest["limits"],
            "budgets": {
                k: value["budget"][k]
                for k in ("agent_microusd", "case_generator_microusd", "judge_microusd")
            },
            "fault_profile_id": (manifest.get("fault_profile") or {}).get("id"),
            **({"external_fault": manifest["external_fault"]} if manifest.get("external_fault")
               else {}),
        }
        modes = sorted(
            {
                e.get("timing", {}).get("gateway_mode")
                for e in value["executions"]
                if e.get("timing", {}).get("gateway_mode")
            }
        )
        return {
            "source_run_id": value["run_id"],
            "adapter_type": manifest.get("adapter_type", "langgraph_booking"),
            "target_id": target_id,
            "source_manifest_hash": value["manifest_hash"],
            "configuration": configuration,
            "cases": cases,
            "gateway_modes": modes,
            "boundary": "再次运行创建新记录，保留可取得的原条件，不保证模型逐字相同；"
            "历史回放只读取原记录。",
        }

    def validate(self, owner, plan):
        workbench = WorkbenchService(self.engine, self.settings)
        source = self.source(owner, plan.source_run_id) if plan.source_run_id else None
        delta = (
            differences(
                normalized(CreateRun.model_validate(source["configuration"])),
                normalized(plan.configuration),
            )
            if source
            else []
        )
        if source and plan.action == "rerun":
            expected_mode = (
                "external_http" if is_external(source.get("adapter_type")) else self.mode
            )
            if delta or source["gateway_modes"] and source["gateway_modes"] != [expected_mode]:
                raise AgentError(
                    "rerun_conditions_changed",
                    "原配置或模型来源不同；请选择复制并修改并核对差异，不能冒充原条件复现。",
                    409,
                )
        with Session(self.engine) as session:
            workbench._identity(session, owner)
            if self.mode == "real_provider" and plan.configuration.budgets.agent_microusd <= 0:
                raise AgentError(
                    "run_budget_required",
                    "本次运行预算必须大于0；0表示禁止模型调用。原配置为0时，请复制并修改预算后再创建。",
                    400,
                )
            visible = set(
                session.scalars(
                    workbench.visible_cases(session, owner).with_only_columns(TestCase.id)
                )
            )
            requested = set(plan.configuration.case_revision_ids)
            if plan.action != "rerun" and not requested.issubset(visible):
                raise AgentError(
                    "workflow_case_not_visible", "请选择当前可见的已审核开发修订。", 409
                )
            for identifier in requested:
                row = session.get(TestCase, identifier)
                if row is None or row.dataset_split != "dev":
                    raise AgentError(
                        "workflow_development_only", "此入口仅接受已审核开发修订。", 403
                    )
                snapshot = frozen_case(row)
                if (
                    snapshot["content"]["fixture"]
                    .get("dataset", {})
                    .get("knowledge_profile", "base")
                    != "base"
                ):
                    raise AgentError(
                        "workflow_knowledge_mismatch", "当前创建入口只匹配基础知识条件。", 409
                    )
                if (
                    source
                    and plan.action == "rerun"
                    and not any(
                        c["case_revision_id"] == identifier
                        and c["content_hash"] == snapshot["content_hash"]
                        for c in source["cases"]
                    )
                ):
                    raise AgentError("rerun_case_changed", "原用例内容无法按封存修订核对。", 409)
            _, manifest, _, _ = RunService(self.engine, self.settings).prepare_configuration(
                session, owner, plan.configuration, "preview"
            )
            if self.mode == "real_provider" and not is_external(manifest.get("adapter_type")):
                require_model_call(session, owner)
            if self.mode == "offline_test_double" and is_external(manifest.get("adapter_type")):
                raise AgentError(
                    "external_offline_not_allowed", "离线模型执行器不会连接外部服务。", 409
                )
        return source, delta, manifest

    async def observe(self, plan, manifest=None, *, owner=None):
        report = await self.readiness(self.settings)
        batch = next(x for x in report.operations if x.id == "batch")
        required = (
            batch.required_checks
            if plan.dispatch == "ready"
            else ["api", "mysql", "schema", "redis", "checkpoints", "version"]
        )
        external = bool(manifest and is_external(manifest.get("adapter_type")))
        if external:
            # The chosen target is validated separately; no dependency on booking checkpoints,
            # DeepSeek credentials or its separate authorization ledger is implied.
            required = [
                c
                for c in required
                if c not in {"version", "checkpoints", "credential", "executor_budget"}
            ]
            if plan.dispatch == "ready":
                from app.external.http_port import HttpPort
                from app.external.runner import request
                from app.external.schema import HttpEntry
                from app.external.transport import Client
                from app.services.readiness import observation

                entry = entry_for(self.settings, manifest["adapter_type"], owner=owner)
                binding = {
                    "tenant": canonical_hash(["preflight", entry.id]),
                    "scope": "test:probe:" + uuid4().hex,
                    "session_id": str(uuid4()),
                }
                try:
                    if isinstance(entry, HttpEntry):
                        await asyncio.to_thread(HttpPort(entry, self.settings, owner).verify)
                        ready = self.settings.external_agent_calls_enabled
                    else:
                        actual = await asyncio.to_thread(
                            Client(entry, self.settings, owner=owner).call,
                            request(entry, binding, "capabilities", uuid4(), {}),
                        )
                        ready = canonical_hash(actual) == canonical_hash(entry.card.model_dump())
                except AgentError:
                    ready = False
                report.checks.append(
                    observation(
                        "external_target",
                        "外部服务",
                        "ready" if ready else "blocked",
                        "external_ready" if ready else "external_unavailable",
                        (
                            "已核对接口配置，试跑时检查回复字段。"
                            if isinstance(entry, HttpEntry) else "外部能力与登记一致。"
                        ) if ready else "外部开关、凭据、连接或版本未就绪。",
                        "先到外部 Agent 接入页检查连接，再核对服务版本。",
                    )
                )
                required = [*required, "external_target"]
        if self.mode == "offline_test_double" and plan.dispatch == "ready":
            required = [x for x in required if x not in ("credential", "executor_budget")]
        by_id = {c.id: c for c in report.checks}
        now = datetime.now(UTC)
        blocked = [
            name
            for name in required
            if name not in by_id or by_id[name].status != "ready" or by_id[name].expires_at <= now
        ]
        worker = by_id.get("worker")
        if plan.dispatch == "ready" and worker and worker.facts.get("observed_mode") != self.mode:
            blocked.append("worker")
        return report, list(dict.fromkeys(blocked))

    async def preview(self, owner, plan):
        source, delta, manifest = self.validate(owner, plan)
        report, blockers = await self.observe(plan, manifest, owner=owner)
        identifier = secrets.token_hex(32)
        now = datetime.now(UTC)
        context = {
            "plan_id": identifier,
            "action": plan.action,
            "source_run_id": str(plan.source_run_id) if source else None,
            "source_manifest_hash": source["source_manifest_hash"] if source else None,
            "differences": delta,
            "dispatch": plan.dispatch,
            "declared_gateway_mode": "external_http"
            if is_external(manifest.get("adapter_type"))
            else self.mode,
            "queue_deadline": (
                now + timedelta(seconds=plan.configuration.limits.timeout_seconds)
            ).isoformat(),
            "source_modes": source["gateway_modes"] if source else [],
        }
        record = {
            "owner": owner,
            "request": plan.model_dump(mode="json"),
            "environment": self.environment(),
            "workflow": context,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=60)).isoformat(),
            "configuration_hash": canonical_hash(manifest),
        }
        if not blockers:
            with self.client() as client:
                client.set(self.key(owner, identifier), json.dumps(record), ex=90)
        return {
            "plan_id": identifier if not blockers else None,
            "request_id": str(plan.configuration.request_id),
            "expires_at": record["expires_at"],
            "queue_deadline": context["queue_deadline"],
            "ready": not blockers,
            "blockers": blockers,
            "checks": [c.model_dump(mode="json") for c in report.checks],
            "configuration": plan.configuration.model_dump(mode="json"),
            "effective_limits": manifest["limits"],
            "workflow": context,
            "mode_label": "外部 HTTP 对话服务"
            if is_external(manifest.get("adapter_type"))
            else "明确离线受控模型"
            if self.mode == "offline_test_double"
            else "真实模型执行器",
            "boundary": "仅排队不表示已执行；Worker、模型开关和预算仍在实际执行时检查。"
            if plan.dispatch == "queue_only"
            else "预检不执行模型或工具；创建时重新检查，实际执行仍受租约、确认与预算保护。",
        }

    async def commit(self, owner, body):
        with Session(self.engine) as session:
            WorkbenchService(self.engine, self.settings)._identity(session, owner)
            existing = session.scalar(
                select(TestRun).where(TestRun.run_uuid == str(body.request_id))
            )
            if existing:
                if existing.created_by != owner:
                    raise AgentError("run_not_found", "当前身份下找不到运行。", 404)
                if (existing.manifest_json.get("workflow") or {}).get("plan_id") != body.plan_id:
                    raise AgentError(
                        "run_request_conflict",
                        "请求标识已用于另一份配置，请先查证原创建回执。",
                        409,
                    )
                return {"run_id": existing.run_uuid, "receipt_reused": True}
        with self.client() as client:
            raw = client.get(self.key(owner, body.plan_id))
        if not raw:
            raise AgentError("run_plan_expired", "预检不存在或已过期，请重新核对配置。", 409)
        record = json.loads(raw)
        if record["owner"] != owner or record["request"]["configuration"]["request_id"] != str(
            body.request_id
        ):
            raise AgentError("run_plan_mismatch", "预检身份或请求标识不一致。", 409)
        if (
            datetime.fromisoformat(record["expires_at"]) <= datetime.now(UTC)
            or record["environment"] != self.environment()
        ):
            raise AgentError("run_plan_expired", "环境、源码或预检期限已变化，请重新检查。", 409)
        plan = RunPlan.model_validate(record["request"])
        _, _, manifest = self.validate(owner, plan)
        if canonical_hash(manifest) != record["configuration_hash"]:
            raise AgentError("run_configuration_changed", "用例或执行条件已变化，请重新检查。", 409)
        _, blocked = await self.observe(plan, manifest, owner=owner)
        if blocked:
            raise AgentError(
                "run_preflight_blocked", "当前执行条件未就绪：" + "、".join(blocked), 409
            )
        if datetime.fromisoformat(record["workflow"]["queue_deadline"]) <= datetime.now(UTC):
            raise AgentError("run_queue_expired", "本次排队有效期已过，请重新核对。", 409)
        identifier = RunService(self.engine, self.settings).create(
            owner, plan.configuration, workflow=record["workflow"]
        )
        return {"run_id": identifier, "receipt_reused": False}
