"""Read-only case sections matching the product reader; never evaluate a case."""

import html
import json

SECTIONS = (
    ("prerequisites", "guide-before", "01 · 运行前要知道", "前置条件"),
    ("turns", "guide-inputs", "02 · 智能体会收到什么", "逐轮原文"),
    ("expected", "guide-expected", "03 · 应该得到什么", "预期结果"),
    ("assertions", "guide-rules", "04 · 平台具体检查哪些证据", "检查项"),
)


def esc(value):
    return html.escape(str(value), quote=True)


def raw(value):
    return '<pre tabindex="0">' + esc(json.dumps(value, ensure_ascii=False, indent=2)) + "</pre>"


def facts(value):
    labels = {
        "room_code": "场地",
        "date": "日期",
        "start_at": "开始",
        "start_time": "开始",
        "end_at": "结束",
        "end_time": "结束",
        "attendees": "人数",
        "status": "状态",
    }
    return " · ".join(esc(labels[key]) + "：" + esc(value[key]) for key in labels if key in value)


def prerequisites(guide):
    value = guide.get("prerequisites", {})
    initial = value.get("initial_state") or {}
    bookings = initial.get("active_bookings")
    fields = [
        ("参考时钟", value.get("reference_clock")),
        ("知识条件", value.get("knowledge_profile")),
        ("业务夹具", value.get("fixture")),
        (
            "初始有效预约",
            str(len(bookings)) + " 条（用例声明）" if isinstance(bookings, list) else None,
        ),
    ]
    body = (
        '<dl class="case-conditions">'
        + "".join(
            "<div><dt>"
            + label
            + "</dt><dd>"
            + esc(item if item is not None else "未记录")
            + "</dd></div>"
            for label, item in fields
        )
        + "</dl>"
    )
    rooms = initial.get("rooms")
    if isinstance(rooms, list) and rooms:
        body += (
            '<div class="case-table-scroll"><table><thead><tr>'
            "<th>场地</th><th>容量</th><th>开放</th></tr></thead><tbody>"
        )
        for room in rooms:
            if isinstance(room, dict):
                body += (
                    "<tr><td>"
                    + esc(room.get("code", "未记录"))
                    + "</td><td>"
                    + esc(room.get("capacity", "未记录"))
                    + "</td><td>"
                    + esc(str(room.get("opens", "?")) + "—" + str(room.get("closes", "?")))
                    + "</td></tr>"
                )
        body += "</tbody></table></div>"
    if value.get("fault_note"):
        body += '<p class="copy">' + esc(value["fault_note"]) + "</p>"
    return body


def turns(guide):
    return (
        '<ol id="case-turns" aria-label="完整逐轮输入">'
        + "".join(
            '<li><span class="case-turn-number">第 '
            + esc(turn["number"])
            + ' 轮</span><p class="copy">'
            + esc(turn["user"])
            + "</p></li>"
            for turn in guide.get("turns", [])
        )
        + "</ol>"
    )


def expected(guide):
    value = guide.get("expected", {})
    body = (
        '<p class="copy">' + esc(guide["reviewed_reason"]) + "</p>"
        if guide.get("reviewed_reason")
        else ""
    )
    counts = value.get("active_booking_counts")
    if isinstance(counts, list) and counts:
        body += "<p>允许的最终有效预约数：" + esc(" 或 ".join(map(str, counts))) + " 条。</p>"
    goal = value.get("task_goal")
    if isinstance(goal, dict) and goal:
        body += "<p>" + facts(goal) + "</p>"
    for item in value.get("permitted_booking_facts") or []:
        body += "<p>" + facts(item) + "</p>"
    if not counts and not goal:
        body += "<p>此修订未单列目标或允许数量，请核对原始断言。</p>"
    if value.get("boundary"):
        body += '<p class="cost">' + esc(value["boundary"]) + "</p>"
    checks = value.get("manual_checks") or []
    if checks:
        body += "<ul>" + "".join("<li>" + esc(item) + "</li>" for item in checks) + "</ul>"
    if value.get("retrieval_review"):
        body += (
            "<details><summary>人工检索核对</summary>"
            + raw(value["retrieval_review"])
            + "</details>"
        )
    return body


def assertions(guide):
    rows = guide.get("assertions") or []
    if not rows:
        return "<p>此修订未列出检查项。</p>"
    body = '<ul class="case-checks">'
    for row in rows:
        body += (
            "<li><strong>"
            + esc(row.get("title", row.get("type", "未命名检查")))
            + '</strong><p class="copy">'
            + esc(row.get("meaning", "未提供解释"))
            + '</p><p class="cost">依据：'
            + esc(row.get("evidence", "未记录"))
            + "</p><details><summary>原始断言</summary>"
            + raw(row.get("parameters"))
            + "</details></li>"
        )
    return body + "</ul>"


def render_case(task):
    guide = task["snapshot"]["guidance"]
    body = '<details id="case-read" open><summary>查看所选用例说明</summary>'
    case_key = guide.get("case_key") or task.get("case_key", "")
    revision = guide.get("revision", task["snapshot"].get("content", {}).get("revision"))
    if case_key:
        body += (
            '<p class="cost">'
            + esc(case_key)
            + ((" · 修订 " + esc(revision)) if revision is not None else "")
            + "</p>"
        )
    builders = {
        "prerequisites": prerequisites,
        "turns": turns,
        "expected": expected,
        "assertions": assertions,
    }
    for key, target, title, _ in SECTIONS:
        opened = " open" if key in {"turns", "expected"} else ""
        body += (
            f'<details class="case-part"{opened}>'
            f'<summary id="{target}-label">{title}</summary>'
            f'<div id="{target}" class="case-section" tabindex="-1" '
            f'role="region" aria-labelledby="{target}-label">'
            + builders[key](guide)
            + "</div></details>"
        )
    body += (
        '<details class="case-raw"><summary>原始修订与字段</summary>'
        + raw(task["snapshot"])
        + "</details></details>"
    )
    return body


def citation_links(task, result):
    answer = result.get("answer") or {}
    cited = {
        reference
        for key in ("facts", "possible_causes", "missing_evidence", "next_steps")
        for item in answer.get(key, [])
        if isinstance(item, dict)
        for reference in item.get("citations", [])
        if isinstance(reference, str)
    }
    for source in result.get("sources", []):
        content = source.get("content") or {}
        if not isinstance(content, dict):
            continue
        if (
            source.get("source_id") not in cited
            or source.get("tool") != "read_selection"
            or content.get("kind") != "case"
            or str(content.get("id")) != str(task.get("case_id"))
            or not task["snapshot"].get("content_hash")
            or content.get("content_hash") != task["snapshot"]["content_hash"]
        ):
            continue
        locations = content.get("locations") or {}
        links = [
            f'<a class="source-ref" href="#{target}" aria-label="查看证据：{label}">{label}</a>'
            for key, target, _, label in SECTIONS
            if isinstance(locations, dict) and isinstance(locations.get(key), str)
        ]
        return (
            '<nav class="source-links" aria-label="引用原文定位">' + "".join(links) + "</nav>"
            if links
            else ""
        )
    return ""
