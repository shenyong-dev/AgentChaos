"""Explain persisted results without evaluating, scoring, or invoking a model."""

from collections import Counter, defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.db.engine import read_session
from app.evaluation.engine import Evidence, Missing
from app.evaluation.service import input_hash, load_evidence
from app.evidence.recorder import redact
from app.executors.service import RunService
from app.models import AssertionResult, TargetVersion, TestExecution, TraceEvent
from app.reports.analysis import latest_rows, statistics, verdict

DIMENSIONS = (
    ("tools", "工具契约", {"tool_contract"}, "已观察工具请求的参数是否满足服务端工具契约。"),
    (
        "confirmation",
        "确认绑定",
        {"confirmation_binding"},
        "实际确认或写入流程是否绑定当前草稿与用户确认。",
    ),
    (
        "parameters",
        "参数与字段一致性",
        {"state_matches", "booking_matches", "tool_calls"},
        "仅核对用例声明的字段、预约条件或工具参数。",
    ),
    (
        "uniqueness",
        "预约唯一性",
        {"unique_bookings"},
        "存在有效预约时，检查原请求和同场地时段的唯一性。",
    ),
    (
        "recovery",
        "未知提交查证",
        {"original_request_recovery"},
        "未知提交后，原请求查证是否与SQL事实闭环。",
    ),
    (
        "consistency",
        "业务声明与事实",
        {"business_consistent", "reply_booking_consistent"},
        "仅核对已实现的结构化声明或可解析预约回复，不推断通用语义。",
    ),
)
ELIGIBLE = {"passed", "failed", "inconclusive"}
ISSUES = {
    "execution_start_missing": "缺少执行开始事件",
    "execution_end_missing": "缺少执行结束事件",
    "execution_not_terminal": "执行尚未结束",
    "business_after_snapshot_missing": "缺少执行后SQL快照",
    "business_before_snapshot_missing": "缺少执行前SQL快照",
    "agent_output_missing": "缺少最终Agent输出",
    "tool_request_result_pair_missing": "工具请求与返回不完整",
    "model_observation_missing": "模型请求与返回不完整",
    "case_hash_mismatch": "用例快照与原指纹不一致",
    "business_snapshot_invalid": "SQL快照结构不完整",
    "run_event_sequence_gap": "运行事件序列不完整",
    "planned_execution_gap": "计划与持久执行数量不一致",
    "saved_evaluation_metadata_invalid": "保存的评价版本或任务结论无法核对",
    "saved_evaluation_input_changed": "保存判定的输入指纹与当前证据不一致",
    "agent_output_invalid": "最终Agent输出结构不完整",
}


def booking_count(value):
    try:
        return len(Evidence("", None, value, None, None).bookings())
    except (ValueError, TypeError, KeyError, AttributeError, Missing):
        return None


def assertion(row):
    return {
        "id": row.id,
        "key": row.assertion_key,
        "metric": row.metric_name,
        "evaluator_kind": row.evaluator_kind,
        "evaluator_version": row.evaluator_version,
        "verdict": row.verdict,
        "expected": row.expected_json,
        "actual": row.actual_json if isinstance(row.actual_json, dict) else {},
        "reason": row.reason,
        "evidence_refs": row.evidence_refs_json,
    }


def code_version(row):
    evaluator = row.get("actual", {}).get("evaluator")
    value = evaluator.get("version") if isinstance(evaluator, dict) else None
    return value if isinstance(value, str) and value else "unrecorded"


def saved_input_matches(bundle, dsl, rows):
    digest = input_hash(bundle, dsl.get("assertions", []), dsl.get("task_goal"))
    return all(row["actual"].get("input_hash") == digest for row in rows)


def dimension(entry, identifier, types):
    rows = [
        row
        for row in latest_rows(entry["assertions"])
        if row["metric"] in types and row["verdict"] != "na"
    ]
    if identifier == "tools" and not entry["behavior"]["tool_requests"]:
        rows = []
    if (
        identifier == "confirmation"
        and not entry["behavior"]["confirmation_events"]
        and not entry["behavior"]["write_requests"]
    ):
        rows = []
    if identifier == "uniqueness" and not entry["business"]["active_bookings"]:
        rows = []
    if identifier == "parameters":
        rows = [
            row
            for row in rows
            if row["metric"] != "tool_calls"
            or (
                isinstance(row.get("expected"), dict)
                and bool(row["expected"].get("arguments"))
                and entry["behavior"].get("tool_counts", {}).get(row["expected"].get("tool"), 0) > 0
            )
        ]
    if entry["status"] not in ELIGIBLE:
        status = "excluded"
    elif not rows:
        status = "untested"
    elif not entry["evidence_integrity"]["complete"]:
        status = "inconclusive"
    else:
        status = verdict(rows)
    return {
        "execution_id": entry["execution_id"],
        "case_key": entry["case_key"],
        "status": status,
        "assertion_ids": [row["id"] for row in rows],
        "evaluator_version": rows[0]["evaluator_version"] if rows else None,
    }


def projection(entries):
    groups = defaultdict(list)
    for entry in entries:
        rows = latest_rows(entry["assertions"])
        versions = sorted({code_version(row) for row in rows})
        groups[
            (entry["gateway_mode"] or "unrecorded", entry["dataset_split"], tuple(versions))
        ].append(entry)
    cohorts = []
    for (mode, split, versions), members in groups.items():
        dimensions = []
        for identifier, title, types, definition in DIMENSIONS:
            samples = [dimension(entry, identifier, types) for entry in members]
            counts = Counter(sample["status"] for sample in samples)
            dimensions.append(
                {
                    "id": identifier,
                    "title": title,
                    "definition": definition,
                    "counts": dict(counts),
                    "denominator": counts["passed"] + counts["failed"],
                    "samples": samples,
                }
            )
        cohorts.append(
            {
                "id": canonical_hash([mode, split, versions])[:16],
                "gateway_mode": mode,
                "dataset_split": split,
                "evaluator_versions": list(versions),
                "statistics": statistics(members),
                "dimensions": dimensions,
                "execution_ids": [entry["execution_id"] for entry in members],
            }
        )
    return cohorts


class ReliabilityService:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    def read(self, owner, identifier):
        with read_session(self.engine) as session:
            run = RunService.owned_run(session, owner, identifier)
            manifest = run.manifest_json
            if canonical_hash(manifest) != run.manifest_hash:
                raise AgentError(
                    "reliability_manifest_integrity", "运行清单哈希不符，摘要不可用。", 503
                )
            target = session.get(TargetVersion, run.target_version_id)
            if (
                not target
                or manifest.get("target_version_id") != target.id
                or manifest.get("target") != target.model_config_json
                or canonical_hash(
                    {
                        key: value
                        for key, value in target.model_config_json.items()
                        if key != "config_hash"
                    }
                )
                != target.config_hash
            ):
                raise AgentError(
                    "reliability_target_integrity", "冻结对象版本不一致，摘要不可用。", 503
                )
            executions = list(
                session.scalars(
                    select(TestExecution)
                    .where(TestExecution.run_id == run.id)
                    .order_by(TestExecution.id)
                )
            )
            planned = len(manifest.get("cases", [])) * manifest.get("repetitions", 0)
            event_count, low, high = session.execute(
                select(
                    func.count(), func.min(TraceEvent.run_seq_no), func.max(TraceEvent.run_seq_no)
                ).where(TraceEvent.run_id == run.id)
            ).one()
            if event_count > 50_000 or len(executions) > 600:
                raise AgentError(
                    "reliability_read_limit", "记录超出摘要读取上限，请使用完整报告分段核对。", 503
                )
            sequence_ok = event_count == run.event_seq and (
                not event_count or (low == 1 and high == run.event_seq)
            )
            all_rows = list(
                session.scalars(
                    select(AssertionResult)
                    .join(TestExecution)
                    .where(TestExecution.run_id == run.id)
                    .order_by(AssertionResult.id)
                )
            )
            if len(all_rows) > 120_000:
                raise AgentError(
                    "reliability_read_limit", "判定记录超出摘要上限，请使用完整报告核对。", 503
                )
            by_execution = defaultdict(list)
            for row in all_rows:
                by_execution[row.execution_id].append(assertion(row))
            entries = []
            for execution in executions:
                bundle = load_evidence(session, run, execution, lock_events=False)
                case = execution.input_snapshot_json
                content, dsl = case.get("content", {}), case.get("dsl", {})
                issues = list(bundle.issues)
                if not sequence_ok:
                    issues.append("run_event_sequence_gap")
                if planned != len(executions):
                    issues.append("planned_execution_gap")
                if canonical_hash(content) != case.get("content_hash"):
                    issues.append("case_hash_mismatch")
                active, before = booking_count(bundle.after), booking_count(bundle.before)
                if (bundle.after is not None and active is None) or (
                    bundle.before is not None and before is None
                ):
                    issues.append("business_snapshot_invalid")
                complete = bundle.complete and not issues and not bundle.integrity_errors
                events = bundle.events or []
                counts = Counter(event["type"] for event in events)
                rules = latest_rows(by_execution[execution.id])
                if rules and not saved_input_matches(bundle, dsl, rules):
                    issues.append("saved_evaluation_input_changed")
                    complete = False
                if bundle.output is not None and not isinstance(bundle.output, dict):
                    issues.append("agent_output_invalid")
                    complete = False
                outcome = (
                    rules[0].get("actual", {}).get("task_outcome", "unknown")
                    if rules
                    else "unknown"
                )
                if rules and (
                    any(code_version(row) == "unrecorded" for row in rules)
                    or outcome not in {"fulfilled", "not_fulfilled", "unknown", "safely_degraded"}
                    or any(row["actual"].get("task_outcome") != outcome for row in rules)
                ):
                    issues.append("saved_evaluation_metadata_invalid")
                    complete = False
                    outcome = "unknown"
                if execution.status not in ELIGIBLE:
                    assessment = (
                        "pending"
                        if execution.status in {"queued", "running", "waiting_input"}
                        else "platform_error"
                        if execution.status == "error"
                        else "excluded"
                    )
                else:
                    assessment = "insufficient" if not complete else verdict(rules)
                injected = [
                    event["event_id"] for event in events if event["type"] == "fault.injected"
                ]
                configured = bool(manifest.get("fault_profile"))
                fault_state = (
                    "injected"
                    if injected
                    else "not_triggered"
                    if configured and complete
                    else "unconfirmed"
                    if configured
                    else "control"
                )
                code = code_version(rules[0]) if rules else None
                entries.append(
                    {
                        "execution_id": execution.execution_uuid,
                        "case_key": content.get("case_key"),
                        "case_hash": case.get("content_hash"),
                        "case_revision_id": execution.case_revision_id,
                        "repeat_no": execution.repeat_no,
                        "attempt": execution.attempt_no,
                        "status": execution.status,
                        "gateway_mode": (execution.timing_json or {}).get("gateway_mode"),
                        "dataset_split": content.get("dataset_split", "unrecorded"),
                        "category": content.get("category"),
                        "reference_clock": dsl.get("reference_clock"),
                        "fixture": dsl.get("fixture"),
                        "goal": dsl.get("task_goal"),
                        "definitions": dsl.get("assertions", []),
                        "turns": dsl.get("turns", []),
                        "assessment": assessment,
                        "rule_verdict": verdict(rules),
                        "evaluator_version": rules[0]["evaluator_version"] if rules else None,
                        "code_version": code,
                        "assertions": by_execution[execution.id],
                        "saved_rules": rules,
                        "usage": execution.usage_json,
                        "evidence_integrity": {
                            "complete": complete,
                            "issues": issues,
                            "errors": bundle.integrity_errors,
                        },
                        "evidence_messages": [
                            ISSUES.get(issue, issue) for issue in issues + bundle.integrity_errors
                        ],
                        "behavior": {
                            "tool_counts": dict(
                                Counter(
                                    event["payload"].get("tool")
                                    for event in events
                                    if event["type"] == "tool.requested"
                                )
                            ),
                            "tool_requests": counts["tool.requested"],
                            "tool_errors": counts["tool.error"],
                            "confirmation_events": counts["confirmation.requested"]
                            + counts["confirmation.resumed"],
                            "write_requests": sum(
                                event["type"] == "tool.requested"
                                and event["payload"].get("tool") == "confirm_booking"
                                for event in events
                            ),
                            "model_errors": counts["model.error"],
                            "reply": bundle.output.get("assistant_text")
                            if isinstance(bundle.output, dict)
                            else None,
                            "event_ids": [
                                event["event_id"]
                                for event in events
                                if event["type"]
                                in {
                                    "tool.error",
                                    "model.error",
                                    "confirmation.requested",
                                    "execution.error",
                                }
                            ][:20],
                        },
                        "business": {
                            "active_bookings": active,
                            "before_bookings": before,
                            "task_outcome": outcome
                            if complete and execution.status in ELIGIBLE
                            else "unknown",
                        },
                        "fault": {
                            "state": fault_state,
                            "injected": len(injected),
                            "event_ids": injected[:20],
                            "not_applied": counts["fault.not_applied"],
                            "scheduled": counts["fault.scheduled"],
                        },
                        "ai": {
                            "verdict": verdict(latest_rows(by_execution[execution.id], "ai")),
                            "records": len(latest_rows(by_execution[execution.id], "ai")),
                        },
                    }
                )
            result = {
                "schema": "reliability-view-v1",
                "run_id": run.run_uuid,
                "observed_at": datetime.now(UTC).isoformat(),
                "run_status": run.status,
                "manifest_hash": run.manifest_hash,
                "target_version_id": run.target_version_id,
                "event_count": event_count,
                "complete_read": True,
                "planned_executions": planned,
                "execution_status_counts": dict(Counter(entry["status"] for entry in entries)),
                "conditions": {
                    "target_config_hash": manifest.get("target", {}).get("config_hash"),
                    "model": manifest.get("target", {}).get("model_requested"),
                    "knowledge": manifest.get("target", {}).get("retrieval"),
                    "fault_profile": manifest.get("fault_profile"),
                    "seed": manifest.get("seed"),
                    "limits": manifest.get("limits"),
                    "repetitions": manifest.get("repetitions"),
                },
                "cohorts": projection(entries),
                "executions": entries,
                "boundary": (
                    "仅投影已保存判定与当前可核对证据；不重新评价、不调用模型。"
                    "分组统计复用T15定义，重复执行不是独立家族，各维度可重叠；不证明质量提升。"
                    "T16正式实验未观察到改善；T17B辅助评价仍为受限人工辅助，原分歧和历史标签保留。"
                ),
            }
            return redact(result, self.settings)
