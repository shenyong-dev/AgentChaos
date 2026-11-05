"""Server-bound read tools. No model-supplied paths, SQL, IDs or destinations."""

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.evidence.reader import TraceReader
from app.evidence.recorder import redact
from app.executors.service import RunService
from app.guidance.service import GuidanceService, catalog
from app.models import AssertionResult, TestExecution, TraceEvent
from app.schemas.guidance import GuideContext


def clean(value, settings):
    value = redact(value, settings)

    def visit(item):
        if isinstance(item, str):
            item = re.sub(
                r"(?i)(password|api[_-]?key|secret|token)\s*[:=]\s*[^\s,;，；]+",
                r"\1=[REDACTED]",
                item,
            )
            item = re.sub(
                r"(?i)\b(?:mysql|redis|rediss|postgresql)://[^\s]+", "[REDACTED_CONNECTION]", item
            )
            return re.sub(r"[A-Za-z]:\\[^\s\"'<>]+", "[LOCAL_PATH]", item)
        if isinstance(item, dict):
            return {str(k): visit(v) for k, v in item.items()}
        if isinstance(item, list):
            return [visit(v) for v in item]
        return item

    return visit(value)


def bounded(value, settings, limit=6000):
    value = clean(value, settings)
    raw = json.dumps(value, ensure_ascii=False, default=str)
    if len(raw.encode()) <= limit:
        return value
    preview = raw.encode()[: limit - 300].decode("utf-8", errors="ignore")
    return {"truncated": True, "note": "仅提供有界预览；缺失部分不可作为事实。", "preview": preview}


class SourceTools:
    LABELS = {
        "read_guide": "读取当前页指南",
        "read_selection": "读取选中对象",
        "read_environment": "核对环境前置",
        "read_run_evidence": "读取执行证据",
        "read_comparison": "核对比较条件",
    }

    def __init__(self, engine, settings, owner, context, *, readiness=None):
        self.engine, self.settings, self.owner, self.values = engine, settings, owner, context
        self.guide = GuidanceService(engine, settings)
        # Interactions/calibration/generation are metadata-only in this assistant.
        self.guide.interaction_reader = lambda owner, identifier: self.interaction_metadata(
            owner, identifier
        )
        self.observation = self.guide.context(
            owner, GuideContext.model_validate(context.model_dump(exclude={"comparison_run_id"}))
        )
        self.readiness = readiness
        self.sources = {}
        self.trace = []
        self.started = datetime.now(UTC)
        self.restricted = (
            bool(self.observation.get("selection", {}).get("restricted_content"))
            if self.observation.get("selection")
            else False
        )
        if self.restricted:
            raise AgentError(
                "copilot_protected_dataset", "校准和保留集内容不进入开发助手，请使用通用指引。", 403
            )
        if context.comparison_run_id:
            other = context.model_copy(
                update={"object_id": str(context.comparison_run_id), "comparison_run_id": None}
            )
            peer = self.guide.context(
                owner, GuideContext.model_validate(other.model_dump(exclude={"comparison_run_id"}))
            )
            if peer["selection"].get("restricted_content"):
                raise AgentError(
                    "copilot_protected_dataset", "比较目标包含受保护数据，不能进入开发助手。", 403
                )
        self.allowed_actions = []
        from app.schemas.guidance import GuideAction

        for action_id in self.observation["actions"]:
            try:
                self.guide.resolve(
                    owner,
                    GuideAction.model_validate(
                        {
                            **context.model_dump(exclude={"comparison_run_id"}),
                            "action_id": action_id,
                        }
                    ),
                )
                self.allowed_actions.append(action_id)
            except AgentError:
                pass

    def interaction_metadata(self, owner, identifier):
        # Reuse ownership checks without exposing conversation messages to platform AI.
        from app.api.interactive import get_interaction_service

        value = get_interaction_service().view(owner, identifier)
        return {k: value.get(k) for k in ("status", "mode")}

    def schemas(self):
        names = ["read_guide"]
        selected = self.observation.get("selection")
        if selected:
            names.append("read_selection")
        if self.values.page == "environment":
            names.append("read_environment")
        if selected and selected["kind"] in ("run", "execution"):
            names.append("read_run_evidence")
        if self.values.comparison_run_id:
            names.append("read_comparison")
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": self.LABELS[name]
                    + "，只读取服务端当前绑定对象；不接收其他目标。",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            }
            for name in names
        ]

    def add(self, tool, title, data, *, action=None, locator=None):
        data = bounded(data, self.settings)
        source = {
            "tool": tool,
            "title": title,
            "content": data,
            "observed_at": self.started.isoformat(),
            "expires_at": (self.started + timedelta(seconds=60)).isoformat(),
            "catalog_hash": self.observation["catalog_hash"],
            "locator": locator,
            "action_id": action if action in self.allowed_actions else None,
        }
        source["content_hash"] = canonical_hash(data)
        source["source_id"] = tool + ":" + canonical_hash(source)[:24]
        self.sources[source["source_id"]] = source
        self.trace.append(
            {
                "tool": tool,
                "label": self.LABELS[tool],
                "source_id": source["source_id"],
                "status": "read",
                "bytes": len(json.dumps(data, ensure_ascii=False).encode()),
            }
        )
        return source

    def invoke(self, name, arguments):
        if name not in {v["function"]["name"] for v in self.schemas()} or arguments != {}:
            raise AgentError("copilot_tool_forbidden", "助手请求不在当前只读工具白名单内。", 403)
        method = getattr(self, name)
        return method()

    def read_guide(self):
        value, _ = catalog()
        return self.add(
            "read_guide",
            value["pages"][self.values.page]["title"] + " · 使用指南",
            {
                "version": value["version"],
                "page": value["pages"][self.values.page],
                "terms": value["terms"],
                "allowed_action_ids": self.allowed_actions,
                "independent_labeling_boundary": self.values.page in ("judging", "semantic"),
            },
            locator={"kind": "guide", "id": value["version"]},
        )

    def read_selection(self):
        value = self.observation.get("selection")
        return self.add(
            "read_selection",
            value["title"],
            value,
            action="case.prepare" if value["kind"] == "case" else "run.trace",
            locator={"kind": value["kind"], "id": value["id"]},
        )

    def read_environment(self):
        if self.readiness:
            report = self.readiness()
        else:
            from app.services.readiness import collect_readiness

            report = asyncio.run(collect_readiness(self.settings)).model_dump(mode="json")
        checks = [
            {
                k: c.get(k)
                for k in (
                    "id",
                    "label",
                    "status",
                    "code",
                    "message",
                    "next_step",
                    "checked_at",
                    "expires_at",
                )
            }
            for c in report["checks"]
        ]
        return self.add(
            "read_environment",
            "环境前置 · 实时检查",
            {
                "checks": checks,
                "checked_at": report["checked_at"],
                "expires_at": report["expires_at"],
                "operation_states": [
                    {"id": o["id"], "status": o["status"], "blockers": o["blockers"]}
                    for o in report["operations"]
                ],
            },
            action="environment.worker",
            locator={"kind": "readiness", "id": report["checked_at"]},
        )

    def read_run_evidence(self):
        selection = self.observation["selection"]
        reader = TraceReader(self.engine, self.settings)
        run_id = selection["run_id"]
        snapshot = reader.snapshot(self.owner, run_id)
        selected_id = selection["id"] if selection["kind"] == "execution" else None
        executions = [
            e for e in snapshot["executions"] if not selected_id or e["execution_id"] == selected_id
        ]
        details = []
        with Session(self.engine) as session:
            run = RunService.owned_run(session, self.owner, run_id)
            query = select(TestExecution).where(TestExecution.run_id == run.id)
            if selected_id:
                query = query.where(TestExecution.execution_uuid == selected_id)
            selected_executions = list(session.scalars(query.order_by(TestExecution.id).limit(4)))
            for e in selected_executions:
                if e.input_snapshot_json.get("content", {}).get("dataset_split") != "dev":
                    raise AgentError("copilot_protected_dataset", "非开发执行不可进入助手。", 403)
                assertions = list(
                    session.scalars(
                        select(AssertionResult)
                        .where(
                            AssertionResult.execution_id == e.id,
                            AssertionResult.evaluator_kind == "rule",
                        )
                        .order_by(AssertionResult.id)
                        .limit(10)
                    )
                )
                details.append(
                    {
                        "execution_id": e.execution_uuid,
                        "status": e.status,
                        "before_sql_snapshot": e.before_state_json,
                        "after_sql_snapshot": e.after_state_json,
                        "final_output": e.final_output_json,
                        "rule_assertions": [
                            {
                                "key": a.assertion_key,
                                "verdict": a.verdict,
                                "expected": a.expected_json,
                                "actual": a.actual_json,
                                "reason": a.reason,
                            }
                            for a in assertions
                        ],
                    }
                )
            event_query = select(TraceEvent).where(TraceEvent.run_id == run.id)
            if selected_id:
                event_query = event_query.where(
                    TraceEvent.execution_id.in_([e.id for e in selected_executions])
                )
            errors = list(
                session.scalars(
                    event_query.where(
                        TraceEvent.event_type.in_(("execution.error", "tool.error", "model.error"))
                    )
                    .order_by(TraceEvent.run_seq_no)
                    .limit(4)
                )
            )
            latest = list(
                session.scalars(event_query.order_by(TraceEvent.run_seq_no.desc()).limit(8))
            )
            chosen = list({e.event_uuid: e for e in errors + latest}.values())
            first_error = errors[0].event_uuid if errors else None
        events = [reader.event(self.owner, run_id, e.event_uuid)["event"] for e in chosen]
        data = {
            "run_id": run_id,
            "manifest_hash": snapshot["manifest_hash"],
            "status": snapshot["status"],
            "executions_total": len(executions),
            "first_error_event_id": first_error,
            "executions_preview": details,
            "events_preview": events,
            "event_scope_total": snapshot["event_count"],
            "preview_policy": "首4条实际错误及最近8条事件；不是完整轨迹。最多4个执行。",
            "sql_missing_is_not_zero": True,
        }
        return self.add(
            "read_run_evidence",
            "开发执行 · SQL与事件快照",
            data,
            action="run.trace",
            locator={
                "kind": "run",
                "id": run_id,
                "event_ids": [i["event_id"] for i in events],
                "first_error_event_id": first_error,
            },
        )

    def read_comparison(self):
        from app.reports.service import ReportService

        result = ReportService(self.engine, self.settings).comparison(
            self.owner, self.observation["selection"]["run_id"], str(self.values.comparison_run_id)
        )
        data = {
            k: result.get(k)
            for k in (
                "left_run",
                "right_run",
                "conditions_match",
                "checks",
                "target_changes",
            )
        }

        def compact(value):
            raw = json.dumps(value, ensure_ascii=False, default=str).encode()
            return (
                value if len(raw) <= 120 else {"sha256": canonical_hash(value), "bytes": len(raw)}
            )

        data["checks"] = [
            {**check, "left": compact(check["left"]), "right": compact(check["right"])}
            for check in result["checks"]
        ]
        data.pop("target_changes", None)
        data["target_changed_keys"] = list(result.get("target_changes", {}))
        data["detail_policy"] = "较长条件值只提供哈希和长度；不据此猜测具体内容。"
        return self.add(
            "read_comparison",
            "两次开发运行 · 比较条件",
            data,
            locator={"kind": "comparison", "id": str(self.values.comparison_run_id)},
        )
