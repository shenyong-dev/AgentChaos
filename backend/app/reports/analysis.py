"""Pure report calculations. No model calls, inferred causal labels or rewritten verdicts."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from app.agents.versions import canonical_hash

TERMINAL = {"passed", "failed", "inconclusive", "error", "cancelled", "skipped"}


def reporter_manifest():
    return {
        "version": "T15-v1",
        "sources": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(__file__).parent.glob("*.py"))
        },
    }


def encoded(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n"
    ).encode()


def latest_rows(rows, kind="rule"):
    found = [row for row in rows if row["evaluator_kind"] == kind]
    if not found:
        return []
    version = max(found, key=lambda row: row["id"])["evaluator_version"]
    return [row for row in found if row["evaluator_version"] == version]


def verdict(rows):
    if not rows:
        return "not_evaluated"
    for status in ("error", "failed", "inconclusive"):
        if any(row["verdict"] == status for row in rows):
            return status
    return "passed" if any(row["verdict"] == "passed" for row in rows) else "na"


def statistics(executions):
    statuses, rules, goals, ai = Counter(), Counter(), Counter(), Counter()
    attempts = 0
    usage = []
    for execution in executions:
        status = execution["status"]
        statuses[status] += 1
        attempts += execution["attempt"]
        rows = latest_rows(execution["assertions"])
        result = verdict(rows)
        eligible = (
            status in {"passed", "failed", "inconclusive"}
            and execution["evidence_integrity"]["complete"]
        )
        excluded = (
            "excluded_" + status
            if status not in {"passed", "failed", "inconclusive"}
            else "evidence_incomplete"
        )
        rules[result if eligible else excluded] += 1
        goal = (rows[0].get("actual", {}).get("task_outcome") if rows else None) or "unknown"
        goals[goal if eligible else excluded] += 1
        ai[verdict(latest_rows(execution["assertions"], "ai"))] += 1
        usage.append({"execution_id": execution["execution_id"], "reported": execution["usage"]})
    denominator = rules["passed"] + rules["failed"]
    goal_denominator = goals["fulfilled"] + goals["not_fulfilled"]
    return {
        "planned_executions": len(executions),
        "unique_cases": len({execution["case_hash"] for execution in executions}),
        "attempts_including_recovery": attempts,
        "execution_status_counts": dict(statuses),
        "rule_outcomes": dict(rules),
        "reliability": {
            "numerator": rules["passed"],
            "denominator": denominator,
            "rate": rules["passed"] / denominator if denominator else None,
            "unit": "execution, latest rule version; repeats are not independent families",
            "exclusions": {
                key: count for key, count in rules.items() if key not in ("passed", "failed")
            },
        },
        "task_outcomes": dict(goals),
        "task_fulfilment": {
            "numerator": goals["fulfilled"],
            "denominator": goal_denominator,
            "rate": goals["fulfilled"] / goal_denominator if goal_denominator else None,
            "exclusions": {
                key: value
                for key, value in goals.items()
                if key not in {"fulfilled", "not_fulfilled"}
            },
        },
        "ai_assistance_outcomes": dict(ai),
        "usage_records": usage,
        "usage_boundary": (
            "Reported usage is retained verbatim. Missing/unknown is not zero or a provider bill."
        ),
    }


def condition_projection(report):
    manifest = report["run"]["manifest"]
    target = manifest.get("target", {})
    cases = manifest.get("cases")
    fault = manifest.get("fault_profile")
    return {
        "target_id": report["target"]["target_id"],
        "case_hashes": sorted(case.get("content_hash", "missing") for case in cases)
        if cases
        else None,
        "repetitions": manifest.get("repetitions"),
        "seed": manifest.get("seed"),
        "fault": fault.get("config_hash")
        if isinstance(fault, dict)
        else "control"
        if "fault_profile" in manifest
        else None,
        "limits": manifest.get("limits"),
        "run_budgets": {
            key: value
            for key, value in report["run"]["budget"].items()
            if key not in {"authorization_id", "authorization_unlimited"}
        },
        "model": {
            key: target.get(key)
            for key in (
                "provider",
                "base_url",
                "model_requested",
                "thinking",
                "max_output_tokens",
                "sampling",
                "sdk_versions",
            )
        },
        "knowledge_documents": target.get("retrieval", {}).get("documents"),
        "tool_contracts": target.get("tools"),
        "trace_schema": manifest.get("trace_schema"),
        "rule_evaluator": manifest.get("evaluation"),
        "observed_rule_evaluators": sorted(
            {
                canonical_hash(row.get("actual", {}).get("evaluator"))
                for execution in report["executions"]
                for row in latest_rows(execution["assertions"])
            }
        )
        or None,
        "relation_comparator": manifest.get("relation_comparator", "not_applicable"),
        "gateway_modes": sorted(
            {execution.get("gateway_mode") or "unrecorded" for execution in report["executions"]}
        ),
        "external_user_inputs": sorted(
            [
                execution["case_hash"],
                execution["repeat_no"],
                execution.get("external_user_inputs", []),
            ]
            for execution in report["executions"]
        ),
    }


def compare(left, right):
    before, after = condition_projection(left), condition_projection(right)
    checks = []
    for key in before:
        missing = before[key] is None or after[key] is None
        if key in {"case_hashes", "knowledge_documents", "tool_contracts", "limits", "run_budgets"}:
            missing = not before[key] or not after[key]
        if key == "case_hashes":
            missing = missing or any(
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in (before[key] or []) + (after[key] or [])
            )
        if key == "model":
            missing = any(value is None for value in before[key].values()) or any(
                value is None for value in after[key].values()
            )
        if key == "gateway_modes":
            missing = not before[key] or not after[key] or "unrecorded" in before[key] + after[key]
        checks.append(
            {
                "condition": key,
                "left": before[key],
                "right": after[key],
                "status": "missing"
                if missing
                else "match"
                if before[key] == after[key]
                else "mismatch",
            }
        )
    valid = all(check["status"] == "match" for check in checks)
    paired = []
    right_executions = {
        (execution["case_hash"], execution["repeat_no"]): execution
        for execution in right["executions"]
    }
    for execution in left["executions"]:
        peer = right_executions.get((execution["case_hash"], execution["repeat_no"]))
        if peer:
            paired.append(
                {
                    "case_key": execution["case_key"],
                    "repeat_no": execution["repeat_no"],
                    "left_execution": execution["execution_id"],
                    "right_execution": peer["execution_id"],
                    "left_rule": verdict(latest_rows(execution["assertions"])),
                    "right_rule": verdict(latest_rows(peer["assertions"])),
                }
            )
    rates = [report["statistics"]["reliability"]["rate"] for report in (left, right)]
    return {
        "left_run": left["run"]["run_id"],
        "right_run": right["run"]["run_id"],
        "conditions_match": valid,
        "checks": checks,
        "paired_executions": paired,
        "left_statistics": left["statistics"],
        "right_statistics": right["statistics"],
        "reliability_rate_delta": rates[1] - rates[0]
        if valid and all(rate is not None for rate in rates)
        else None,
        "target_changes": {
            key: {
                "left": left["run"]["manifest"].get("target", {}).get(key),
                "right": right["run"]["manifest"].get("target", {}).get(key),
            }
            for key in ("config_hash", "baseline", "prompt_hash", "sources", "context", "retrieval")
            if left["run"]["manifest"].get("target", {}).get(key)
            != right["run"]["manifest"].get("target", {}).get(key)
        },
        "formal_v0_v1_experiment": False,
        "boundary": (
            "Development run comparison of the whole target version. Matching conditions "
            "do not establish a formal V0/V1 experiment, single-factor causality or significance."
        ),
    }


def candidate_fingerprint(report, execution, row):
    return canonical_hash(
        {
            "target_id": report["target"]["target_id"],
            "case_hash": execution["case_hash"],
            "assertion_key": row["key"],
            "evaluator_kind": row["evaluator_kind"],
            "metric": row["metric"],
            "expected": row["expected"],
            "evaluator": row.get("actual", {}).get("evaluator"),
            "fault": report["run"]["manifest"].get("fault_profile"),
        }
    )


def markdown(report):
    stats = report["statistics"]
    reliability = stats["reliability"]
    value = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, default=str)
    fence = "`" * max(
        3, max((len(match.group()) + 1 for match in re.finditer(r"`+", value)), default=3)
    )
    return (
        "# AgentChaos 运行报告\n\n"
        f"运行：{report['run']['run_id']}\n\n"
        f"计划执行 {stats['planned_executions']}；不同用例 {stats['unique_cases']}；"
        f"含恢复尝试 {stats['attempts_including_recovery']}。\n\n"
        f"有效规则通过 {reliability['numerator']} / {reliability['denominator']}；"
        "零分母不提供通过率。\n\n"
        "取消、平台错误、证据不足与AI辅助结论分别计数。复核另存，不覆盖原判定；缺陷候选需人工确认。\n\n"
        "下列冻结JSON保留分母、排除项、配置、执行ID、原始判定与证据引用。完整事件和文件哈希见同批证据ZIP。\n\n"
        f"{fence}json\n{value}\n{fence}\n"
    ).encode()
