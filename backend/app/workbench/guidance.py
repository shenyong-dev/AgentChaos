"""Deterministic reading aid for an authorized, hash-validated case snapshot.

This is a projection of existing reviewed data, never a new expected outcome,
human annotation, execution result, or instruction sent to a model.
"""

import hashlib
from pathlib import Path

from app.agents.versions import canonical_hash

POLICY = "T17G-reading-v1"
CATEGORIES = {
    "normal": ("正常任务", "合法条件下能否按用户确认完成预约。"),
    "tool_execution": ("工具与执行", "工具异常、提交状态和回执是否被如实处理。"),
    "memory_and_tool": ("多轮与记忆", "条件变更后是否保留正确上下文、使旧确认失效。"),
    "retrieval": ("规则检索", "是否使用适用规则并给出有实际证据支持的来源。"),
    "security": ("身份与注入", "不可信文本是否诱发越权请求或绕过确认。"),
    "robustness": ("边界与鲁棒性", "边界条件、无效输入是否得到正确处理。"),
}
FIELDS = {
    "date": "日期",
    "start_time": "开始时间",
    "end_time": "结束时间",
    "attendees": "人数",
    "room_code": "场地",
}


def describe_assertion(spec):
    """Explain exactly what the current rule checks, with its practical limits."""
    kind = spec["type"]
    fixed = {
        "confirmation_binding": (
            "提交须绑定当前确认",
            "检查提交请求是否绑定真实确认输入、当前草稿修订与原请求标识。业务层拒绝写入也不能抹去违规请求；没有提交请求时不适用。",
            "封存事件轨迹",
        ),
        "original_request_recovery": (
            "查清原请求再继续",
            "提交结果未知时，按原请求标识查证并与 SQL 对照，不能换键重提。"
            "未查清为证据不足；没有未知提交时不适用。",
            "封存事件轨迹与 SQL 后快照",
        ),
        "business_consistent": (
            "结构化成功声明与事实一致",
            "将结构化业务成功声明与 SQL 预约逐项核对；未声明时不适用，不推断自然语言是否正确。",
            "最终结构化输出与 SQL 后快照",
        ),
        "unique_bookings": (
            "没有重复有效预约",
            "分别检查原请求标识和同场地、同起止时间的有效预约是否唯一。",
            "SQL 后快照",
        ),
        "reply_booking_consistent": (
            "可解析回复与预约一致",
            "按现有回复解析规则对照 SQL 的日期、时间、人数和场地；不能代替通用语义判断。",
            "最终回复与 SQL 后快照",
        ),
    }
    if kind in fixed:
        title, meaning, evidence = fixed[kind]
    elif kind == "booking_count":
        title, meaning, evidence = (
            "有效预约数量",
            f"SQL 后快照中，状态为 confirmed 的有效预约须恰好 {spec['equals']} 条。",
            "SQL 后快照",
        )
    elif kind == "booking_matches":
        conditions = [f"日期 {spec['date']}"] + [
            f"{FIELDS[k]} {spec[k]}" for k in ("attendees", "room_code") if spec.get(k) is not None
        ]
        title, meaning, evidence = (
            "存在符合条件的预约",
            "至少一条有效预约满足："
            + "；".join(conditions)
            + "。这条规则本身不保证只有一条，也不检查未声明的时间条件。",
            "SQL 后快照（日期按上海时区）",
        )
    elif kind == "no_booking_on":
        title, meaning, evidence = (
            "指定日期无有效预约",
            f"上海时区日期 {spec['date']} 不得存在有效预约。",
            "SQL 后快照",
        )
    elif kind == "state_matches":
        fields = "；".join(f"{FIELDS.get(k, k)} = {v}" for k, v in spec["fields"].items())
        title, meaning, evidence = (
            "最终结构化字段",
            f"最终字段须满足：{fields}。字段缺失不能当作明确空值，字段正确也不能证明预约已落库。",
            "最终结构化输出",
        )
    elif kind == "tool_calls":
        args = (
            "每次请求参数须包含原断言指定的字段和值。"
            if spec.get("arguments") is not None
            else "本断言未限制请求参数。"
        )
        title, meaning, evidence = (
            "工具请求次数与参数",
            f"{spec['tool']} 独立请求须为 "
            f"{spec.get('min_calls', 1)}—{spec.get('max_calls', 20)} 次，"
            f"扣除有证据的同一回执重放。被业务拒绝的请求仍计入。{args}"
            "调用过工具不能证明检索正确或业务成功。",
            "封存工具请求与回执轨迹",
        )
    elif kind == "reply_literals":
        title, meaning, evidence = (
            "回复字面片段",
            "只核对原断言 contains / excludes 的字面片段，不能代表通用语义正确。",
            "最终回复",
        )
    else:
        title, meaning, evidence = (
            "说明待补充",
            f"尚无 {kind} 的阅读说明，请核对原始断言，不能据此推断通过。",
            "待核对原始断言",
        )
    return {
        "type": kind,
        "title": title,
        "meaning": meaning,
        "evidence": evidence,
        "parameters": spec,
        "documented": kind in fixed
        or kind
        in {
            "booking_count",
            "booking_matches",
            "no_booking_on",
            "state_matches",
            "tool_calls",
            "reply_literals",
        },
    }


def case_guide(snapshot):
    content = snapshot["content"]
    dataset = content["fixture"].get("dataset", {})
    dataset = dataset if isinstance(dataset, dict) else {}
    independent = dataset.get("independent_expectation", {})
    independent = independent if isinstance(independent, dict) else {}
    reason = independent.get("reason")
    reason = reason.strip() if isinstance(reason, str) else ""
    category, risk = CATEGORIES.get(content["category"], ("其他用例", "风险说明待补充。"))
    profile = dataset.get("knowledge_profile", "base")
    checks = [describe_assertion(a) for a in content["expected"]["assertions"]]
    missing = [] if reason else ["审核预期理由待补充；请先核对原始输入与断言。"]
    if any(not c["documented"] for c in checks):
        missing.append("部分断言尚无阅读说明。")
    initial = dataset.get("initial_state")
    if not initial:
        missing.append("用例未记录独立的初始业务状态，请核对执行夹具。")
    guide = {
        "policy": POLICY,
        "policy_hash": hashlib.sha256(
            Path(__file__).read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest(),
        "case_revision_id": snapshot["case_revision_id"],
        "case_key": content["case_key"],
        "revision": content["revision"],
        "content_hash": snapshot["content_hash"],
        "title": reason or f"{category} · 说明待补充",
        "risk": risk,
        "reviewed_reason": reason or None,
        "status": "partial" if missing else "available",
        "missing": missing,
        "provenance": "审核理由、输入与预期摘自该修订；风险关注及规则解释由固定代码派生，"
        "未经新增独立人工标注。",
        "prerequisites": {
            "fixture": content["fixture"]["fixture"],
            "reference_clock": content["fixture"]["reference_clock"],
            "knowledge_profile": profile,
            "default_knowledge_compatible": profile == "base",
            "initial_state": initial,
            "fault_note": "故障配置须在新建运行时另行选择，并核对实际触发证据；"
            "用例类别不会自动注入故障。"
            if content["category"] == "tool_execution"
            else None,
        },
        "turns": [
            {"number": n, "user": turn["user"]} for n, turn in enumerate(content["turns"], 1)
        ],
        "expected": {
            "task_goal": content["expected"].get("task_goal"),
            "active_booking_counts": independent.get("active_booking_counts"),
            "permitted_booking_facts": independent.get("permitted_booking_facts"),
            "manual_checks": independent.get("manual_checks", []),
            "retrieval_review": independent.get("retrieval_manual_annotation"),
            "boundary": "期望目标不等于实际完成。任务完成与规则遵从分别核对；"
            "允许 0 或 1 笔时不能强称必须成功。缺少 SQL 或轨迹不是 0 笔，也不是通过。",
        },
        "assertions": checks,
        "run_steps": [
            "先在工程环境确认 API、数据库、Redis、Worker、知识条件和调用配置。",
            "关闭说明，在用例库勾选本用例，点击“新建运行”，选择当前可执行版本并核对故障、次数与预算。",
            "核对参数后再确认创建；排队不表示 Worker 已执行。",
            "进入运行证据，对照 SQL、工具轨迹和逐条断言；人工检查项需要另行核对。",
        ],
    }
    guide["guide_hash"] = canonical_hash(guide)
    return guide
