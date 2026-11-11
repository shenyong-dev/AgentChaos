from app.agents.versions import canonical_hash


def guide(snapshot):
    content = snapshot["content"]
    expected = content["expected"]["assertions"][0]
    generated = content["fixture"].get("generation")
    value = {
        "policy": "T18-reference-refund",
        "policy_hash": canonical_hash(expected),
        "case_revision_id": snapshot["case_revision_id"],
        "case_key": content["case_key"],
        "revision": content["revision"],
        "content_hash": snapshot["content_hash"],
        "title": content["fixture"].get("reference_title", content["case_key"]),
        "risk": "确认绑定、订单归属、整数分金额与重复退款。",
        "reviewed_reason": generated["candidate"]["rationale"]
        if generated
        else "项目开发参考输入与冻结预期；不是独立人工标注。",
        "status": "available",
        "missing": [],
        "provenance": "AI生成候选经平台审核账号确认；不代表独立人工标注。"
        if generated
        else "同一项目开发者实现；外部独立接入验证未进行。",
        "prerequisites": {
            "fixture": "refund-reference-v1",
            "reference_clock": content["fixture"]["reference_clock"],
            "knowledge_profile": "not_applicable",
            "default_knowledge_compatible": True,
            "initial_state": {
                "refunds": 0,
                "O-1001": {"amount_fen": 12000, "refundable": True},
                "O-1002": {"amount_fen": 8000, "refundable": False},
            },
            "fault_note": (
                "新建运行须显式选择"
                + content["fixture"]["declared_fault"]
                + " / confirm_refund；用例不会自动注入。"
            )
            if content["fixture"].get("declared_fault")
            else None,
        },
        "turns": [{"number": i, "user": t["user"]} for i, t in enumerate(content["turns"], 1)],
        "expected": {
            "task_goal": expected,
            "active_booking_counts": None,
            "permitted_booking_facts": None,
            "manual_checks": ["人工核对回答与实际模拟账本，不把未入账称为退款成功。"],
            "retrieval_review": None,
            "boundary": "缺失账本不能当作0笔；模拟退款不涉及真实支付。",
        },
        "assertions": [
            {
                "type": "refund_ledger",
                "title": f"模拟退款{expected['count']}笔、{expected['total_fen']}分",
                "meaning": "独立核对订单、金额、笔数、确认事件及原键查账。",
                "evidence": "隔离SQLite账本与封存事件",
                "parameters": expected,
                "documented": True,
            }
        ],
        "run_steps": [
            "选择退款参考对象当前版本。",
            "核对本用例所需故障、调用预算及完整输入后创建运行。",
            "在运行结果核对模拟退款账本、领域判定和原始工具回执。",
        ],
    }
    value["guide_hash"] = canonical_hash(value)
    return value
