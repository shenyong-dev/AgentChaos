"""Free, credential-safe readiness observations. No reservations or model calls."""

import asyncio
from datetime import UTC, datetime, timedelta

from alembic.config import Config
from alembic.script import ScriptDirectory
from celery import Celery
from redis import Redis
from sqlalchemy import inspect, select, text

from app.agents.budget import AgentBudget
from app.cases.budget import AUTHORIZATION as CASEGEN_AUTHORIZATION
from app.cases.budget import GenerationBudget
from app.core.config import PROJECT_ROOT
from app.core.deployment import (
    database_binding,
    execution_fingerprint,
    probe_engine,
    source_fingerprint,
)
from app.executors.budget import (
    FIELD_SOURCE_AUTHORIZATION,
    PLATFORM_AUTHORIZATION,
    authorization_limit,
)
from app.executors.service import RunService
from app.interactive.budget import AUTHORIZATION as INTERACTIVE_AUTHORIZATION
from app.interactive.budget import InteractionBudget
from app.interactive.budget import authorized as interactive_authorized
from app.judging.budget import AUTHORIZATION as LEGACY_JUDGE_AUTHORIZATION
from app.judging.budget import PROTOCOL_AUTHORIZATION, JudgeBudget
from app.models import Base, Target, TargetVersion
from app.schemas.readiness import OperationReadiness, ReadinessCheck, ReadinessReport
from app.services.health import collect_health

TTL_SECONDS = 60


def observation(identifier, label, status, code, message, next_step, *, source="live", **facts):
    now = datetime.now(UTC)
    return ReadinessCheck(
        id=identifier,
        label=label,
        status=status,
        code=code,
        message=message,
        next_step=next_step,
        checked_at=now,
        expires_at=now + timedelta(seconds=TTL_SECONDS),
        source=source,
        facts=facts,
    )


def database_checks(settings):
    engine = probe_engine(settings)
    try:
        cfg = Config(str(PROJECT_ROOT / "backend/alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "backend/alembic"))
        expected = ScriptDirectory.from_config(cfg).get_current_head()
        with engine.connect() as c:
            actual = c.scalar(text("SELECT version_num FROM alembic_version"))
        missing = set(Base.metadata.tables) - set(inspect(engine).get_table_names())
        schema = observation(
            "schema",
            "数据库迁移",
            "ready" if actual == expected and not missing else "blocked",
            "schema_matches" if actual == expected and not missing else "schema_mismatch",
            "迁移版本与必要表匹配。"
            if actual == expected and not missing
            else "迁移版本或必要表不匹配。",
            "由维护者核对迁移记录；本页不会执行迁移。",
            missing_tables=len(missing),
        )
        if schema.status != "ready":
            return [
                schema,
                observation(
                    "version",
                    "当前源码版本",
                    "unknown",
                    "version_not_checked",
                    "迁移未就绪，未读取对象版本。",
                    "先处理数据库迁移。",
                ),
            ]
        manifest = RunService(engine, settings).current_manifest()
        with engine.connect() as c:
            match = c.scalar(
                select(TargetVersion.id)
                .join(Target)
                .where(
                    TargetVersion.config_hash == manifest["config_hash"],
                    Target.archived_at.is_(None),
                )
                .limit(1)
            )
        return [
            schema,
            observation(
                "version",
                "当前源码版本",
                "ready" if match else "blocked",
                "version_matches" if match else "version_not_registered",
                "存在与当前源码匹配的可用对象版本。" if match else "当前源码尚无匹配对象版本。",
                "由维护者核对源码与版本登记；不要把旧运行自动切换为新版本。",
                matching_version_found=bool(match),
            ),
        ]
    except Exception:
        return [
            observation(
                k,
                label,
                "unknown",
                "database_probe_failed",
                "无法核对，未推定正常。",
                "先检查应用数据库连接与读取权限。",
            )
            for k, label in (("schema", "数据库迁移"), ("version", "当前源码版本"))
        ]
    finally:
        engine.dispose()


def worker_check(settings):
    app = Celery("agentchaos_readiness", broker=settings.redis_url)
    app.conf.update(
        broker_transport_options={
            "global_keyprefix": settings.executor_namespace + ":queue:",
            "socket_connect_timeout": 2,
            "socket_timeout": 2,
        },
        broker_connection_timeout=2,
        broker_connection_retry=False,
    )
    try:
        expected_binding = database_binding(settings)
        expected_source = source_fingerprint()
        replies = app.control.broadcast("agentchaos_readiness", reply=True, timeout=2)
        seen, matching = 0, 0
        for envelope in replies or []:
            for value in envelope.values():
                if not isinstance(value, dict) or value.get("protocol") != "T17C-1":
                    continue
                seen += 1
                if (
                    value.get("source_hash") == expected_source
                    and value.get("database_binding") == expected_binding
                    and value.get("execution_hash") == execution_fingerprint(settings)
                    and isinstance(value.get("queues"), list)
                    and settings.executor_queue in value.get("queues", [])
                ):
                    matching += 1
        return observation(
            "worker",
            "Worker 与执行队列",
            "ready" if matching else "blocked" if seen else "unknown",
            "worker_matches" if matching else "worker_no_matching_reply",
            "收到当前源码、数据库和队列匹配的实时回执。"
            if matching
            else "未收到匹配回执；Worker可能离线、繁忙、版本旧或队列不同，不能由配置推定存活。",
            "核对已启动Worker的源码、数据库、执行配置与队列；无回执不等于已证明进程停止。",
            responders=seen,
            matching_workers=matching,
        )
    except Exception:
        return observation(
            "worker",
            "Worker 与执行队列",
            "unknown",
            "worker_probe_failed",
            "Worker探测未完成，未推定正常。",
            "先检查数据库、Redis及Worker控制通道。",
        )
    finally:
        app.close()


def budget_checks(settings):
    client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    s = settings
    judge_finite = (
        s.judge_budget_authorization_id == PROTOCOL_AUTHORIZATION
        and not s.judge_budget_unlimited
        and 0 < s.judge_budget_usd <= 0.5
    )
    policies = [
        (
            "interactive_budget",
            "交互Agent预算",
            s.allow_paid_model_calls,
            interactive_authorized(s)
            if s.model_budget_authorization_id == INTERACTIVE_AUTHORIZATION
            else s.model_budget_authorization_id == "T04-20261025-agent-1"
            and 0 < s.agent_budget_usd <= 1,
            InteractionBudget(client, s, "readiness").key
            if s.model_budget_authorization_id == INTERACTIVE_AUTHORIZATION
            else AgentBudget(client, s).key,
            s.agent_model,
            s.agent_thinking,
            s.agent_max_output_tokens,
            int(s.agent_budget_usd * 1_000_000),
            120 if s.model_budget_authorization_id == INTERACTIVE_AUTHORIZATION else 400,
            4096,
        ),
        (
            "executor_budget",
            "批量Agent预算",
            s.executor_allow_paid_model_calls,
            authorization_limit(s) != 0,
            f"ac:t05:budget:{s.executor_budget_authorization_id}:agent",
            s.agent_model,
            s.agent_thinking,
            s.agent_max_output_tokens,
            authorization_limit(s),
            80
            if s.executor_budget_authorization_id == FIELD_SOURCE_AUTHORIZATION
            else 60
            if s.executor_budget_authorization_id == PLATFORM_AUTHORIZATION
            else 400
            if authorization_limit(s) > 0
            else None,
            4096,
        ),
        (
            "casegen_budget",
            "用例生成预算",
            s.casegen_allow_paid_model_calls,
            s.casegen_budget_unlimited
            and s.casegen_budget_authorization_id == CASEGEN_AUTHORIZATION,
            GenerationBudget(client, s, "readiness", None).key,
            s.casegen_model,
            s.casegen_thinking,
            s.casegen_max_output_tokens,
            -1,
            None,
            4096,
        ),
        (
            "judge_budget",
            "辅助裁判预算",
            s.judge_allow_paid_model_calls,
            judge_finite
            or (
                s.judge_budget_unlimited
                and s.judge_budget_authorization_id == LEGACY_JUDGE_AUTHORIZATION
            ),
            JudgeBudget(client, s, "readiness", None).key,
            s.judge_model,
            s.judge_thinking,
            s.judge_max_output_tokens,
            int(s.judge_budget_usd * 1_000_000) if judge_finite else -1,
            12 if judge_finite else None,
            1536 if judge_finite else 2048,
        ),
    ]
    results = []
    try:
        for (
            key,
            label,
            enabled,
            authorized,
            redis_key,
            model,
            thinking,
            output,
            requested,
            cap,
            output_cap,
        ) in policies:
            facts = {
                "paid_switch": enabled,
                "authorization_matches": bool(authorized),
                "ledger_checked": False,
                "remaining_microusd": None,
                "calls_remaining": None,
            }
            status, code = "ready", "budget_configured"
            message, step = (
                "配置与持久账本可用；实际请求还需逐次预留。",
                "实际动作仍按权限、确认、批次及运行限制核验。",
            )
            try:
                values = client.hgetall(redis_key) if authorized and enabled else {}
                facts["ledger_checked"] = bool(authorized and enabled)
                ledger = {k.decode(): int(v) for k, v in values.items()}
                if not enabled:
                    status, code, message = (
                        "blocked",
                        "paid_switch_off",
                        "本角色付费调用已关闭，已存记录仍可查看。",
                    )
                    step = "需要新调用时，先确认本项角色预算，再由维护者配置开关。"
                elif (
                    not authorized
                    or model != "deepseek-flash"
                    or thinking != "disabled"
                    or not 1 <= output <= output_cap
                ):
                    status, code, message = (
                        "blocked",
                        "role_configuration_mismatch",
                        "角色授权或受控模型参数未匹配。",
                    )
                    step = "核对本项角色配置；配置凭据不等于已获得调用授权。"
                elif not ledger or not ledger.get("limit"):
                    status, code, message = (
                        "blocked",
                        "budget_ledger_missing",
                        "当前授权缺少持久账本，不能当成有余额。",
                    )
                    step = "核对原账本与初始化记录；已丢失的账本应恢复，不能清零重建。"
                elif (
                    ledger.get("protocol_overrun")
                    or (ledger["limit"] < 0) != (requested < 0)
                    or any(
                        ledger.get(k, 0) < 0
                        for k in ("charged_estimate", "held_upper", "calls_started")
                    )
                ):
                    status, code, message = (
                        "blocked",
                        "budget_protocol_error",
                        "账本协议或金额模式不匹配。",
                    )
                else:
                    remaining = (
                        None
                        if requested < 0
                        else min(ledger["limit"], requested)
                        - ledger.get("charged_estimate", 0)
                        - ledger.get("held_upper", 0)
                    )
                    calls = None if cap is None else max(0, cap - ledger.get("calls_started", 0))
                    facts.update(remaining_microusd=remaining, calls_remaining=calls)
                    if (remaining is not None and remaining <= 0) or calls == 0:
                        status, code, message = (
                            "blocked",
                            "budget_exhausted",
                            "金额余额或调用次数已用完，未知用量仍占预留。",
                        )
                        step = "保留旧账本与失败记录，后续新任务需单独确认额度。"
            except Exception:
                status, code, message, step = (
                    "unknown",
                    "budget_unavailable",
                    "持久账本读取失败，余额未知。",
                    "检查Redis与账本读取权限；不要重建额度。",
                )
            results.append(
                observation(
                    key, label, status, code, message, step, source="configuration", **facts
                )
            )
    finally:
        client.close()
    return results


def operations(checks):
    by_id = {c.id: c for c in checks}
    common = ["api", "mysql", "schema"]
    agent = common + ["redis", "checkpoints", "version", "credential"]
    definitions = [
        (
            "history",
            "查看历史与证据",
            common,
            "不需要模型付费开关或Worker；可见数据仍按账号权限读取。",
            "/runs",
        ),
        (
            "offline",
            "准备离线验证",
            common + ["redis", "checkpoints", "version"],
            "现有独立验证脚本使用明确离线模型；本页未运行演示，不会自动启动实验。",
            "/workbench",
        ),
        (
            "interactive",
            "预约Agent交互调试",
            agent + ["interactive_budget"],
            "真实会话绑定独立实验范围；调用还需登录、版本、T17D预算与确认。",
            "/interactive",
        ),
        (
            "batch",
            "批量真实测试",
            agent + ["worker", "executor_budget"],
            "预检不创建或投递运行，所选用例/版本与每run额度由服务端再次核验。",
            "/workspace",
        ),
        (
            "casegen",
            "AI用例生成",
            common + ["redis", "credential", "casegen_budget"],
            "生成候选仍需人工审核，不能直接作为已验证用例。",
            "/cases",
        ),
        (
            "judge",
            "AI辅助裁判",
            common + ["redis", "credential", "judge_budget"],
            "还需所选批次独立标签先封存；12条T17B校准不开放执行级自动裁判。",
            "/judging",
        ),
        (
            "copilot",
            "平台AI助手",
            ["copilot"],
            "规划于T17E，当前没有可调用助手服务。",
            "/environment",
        ),
    ]
    result = []
    for key, label, required, note, destination in definitions:
        blockers = [name for name in required if by_id[name].status != "ready"]
        states = {by_id[name].status for name in blockers}
        status = (
            "not_available"
            if "not_available" in states
            else "blocked"
            if "blocked" in states
            else "unknown"
            if states
            else "ready"
        )
        result.append(
            OperationReadiness(
                id=key,
                label=label,
                status=status,
                required_checks=required,
                blockers=blockers,
                note=note,
                destination=destination,
            )
        )
    return result


async def collect_readiness(settings):
    health = await collect_health(settings)
    checks = []
    for name, label in (("api", "API服务"), ("mysql", "数据库连接"), ("redis", "Redis连接")):
        item = next(x for x in health.checks if x.name == name)
        ready = item.status == "ok" or name == "redis" and item.code == "redis_capabilities_missing"
        checks.append(
            observation(
                name,
                label,
                "ready" if ready else "blocked",
                item.code,
                "连接已验证。" if ready else item.message,
                "连接正常。" if ready else "核对该服务状态与本地连接配置；本页不会启动或安装服务。",
            )
        )
    redis_check = next(x for x in health.checks if x.name == "redis")
    supported = redis_check.capabilities.get("json", False) and redis_check.capabilities.get(
        "search", False
    )
    checks.append(
        observation(
            "checkpoints",
            "JSON / Search",
            "ready" if supported else "blocked",
            "checkpoint_capabilities_ok" if supported else "checkpoint_capabilities_missing",
            "JSON与Search已实际响应。" if supported else "会话检查点所需能力尚未验证。",
            "核对所连接实例的JSON/Search支持与命令权限。",
        )
    )

    async def guarded(enabled, probe, fallback):
        return await asyncio.to_thread(probe, settings) if enabled else fallback

    mysql_ready = next(c for c in checks if c.id == "mysql").status == "ready"
    redis_ready = next(c for c in checks if c.id == "redis").status == "ready"
    database, budgets, worker = await asyncio.gather(
        guarded(
            mysql_ready,
            database_checks,
            [
                observation(
                    k,
                    label,
                    "unknown",
                    "database_probe_skipped",
                    "数据库不可达，未核对。",
                    "先处理数据库连接。",
                )
                for k, label in (("schema", "数据库迁移"), ("version", "当前源码版本"))
            ],
        ),
        asyncio.to_thread(budget_checks, settings),
        guarded(
            mysql_ready and redis_ready,
            worker_check,
            observation(
                "worker",
                "Worker 与执行队列",
                "unknown",
                "worker_probe_skipped",
                "依赖不可达，未发Worker探测。",
                "先处理数据库和Redis连接。",
            ),
        ),
    )
    checks.extend(database)
    checks.extend(budgets)
    checks.append(worker)
    has_key = bool(settings.deepseek_api_key.get_secret_value())
    checks.append(
        observation(
            "credential",
            "模型凭据",
            "ready" if has_key else "blocked",
            "credential_present" if has_key else "credential_missing",
            "本地凭据存在；没有实时调用模型。" if has_key else "模型凭据未配置。",
            "由维护者在本地配置；不要在页面、聊天或Git中粘贴密钥。",
            source="configuration",
        )
    )
    checks.append(
        observation(
            "copilot",
            "平台助手服务",
            "not_available",
            "copilot_not_implemented",
            "当前尚未实现，计划T17E。",
            "先完成环境引导与预约调试窗口。",
            source="not_implemented",
        )
    )
    now = datetime.now(UTC)
    return ReadinessReport(
        checked_at=now,
        expires_at=min(c.expires_at for c in checks),
        checks=checks,
        operations=operations(checks),
    )
