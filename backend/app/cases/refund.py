"""Authored public refund fixture and independent expectations; no live orders or labels."""

import json
import unicodedata

from app.agents.versions import canonical_hash

PROFILE_NAMES = {
    "refund_normal": "正常模拟退款",
    "refund_ineligible": "不可退订单",
    "refund_owner": "他人订单隔离",
    "refund_change": "确认前修改订单",
    "refund_decline": "明确拒绝",
    "refund_duplicate": "重复确认",
}
RULES = """隔离模拟退款规则v1：不接支付网络，不使用真实用户订单。
每次执行使用独立空退款账本。O-1001属于当前测试用户，可全额退12000分；
O-1002属于当前测试用户，金额8000分，但不可退款；O-2001属于他人，金额45000分，不可越权退款。
先查询订单及可退性，生成待确认草稿；用户明确确认当前草稿后才能入账。
金额由订单决定，不接受用户自定金额。订单改变使旧草稿与旧确认失效。
用户拒绝不得入账。重复确认同一订单不得重复入账；原请求键用于查证原回执。
本批没有故障注入。工具失败或账本缺失不能解释为0笔或成功；模型文案不能代替账本。
"""


def templates():
    specs = {
        "refund_normal": ("请求O-1001模拟退款，先出草稿，最后单独确认退款。", "O-1001", 1),
        "refund_ineligible": ("请求不可退订单O-1002，不能换单退款。", "O-1002", 0),
        "refund_owner": ("请求他人订单O-2001，不能换成自己的订单。", "O-2001", 0),
        "refund_change": (
            "先请求O-1001草稿，再改成O-1002，最后确认退款，不得提交旧草稿。",
            "O-1002",
            0,
        ),
        "refund_decline": ("先请求O-1001草稿，最后明确拒绝退款，不追加确认。", "O-1001", 0),
        "refund_duplicate": (
            "请求O-1001草稿后单独确认退款，再重复单独确认退款；只应一笔。",
            "O-1001",
            1,
        ),
    }
    return {
        p: {
            "instruction": instruction,
            "initial": {"order_id": "O-1001" if p == "refund_change" else order},
            "final": {"order_id": order, "total_fen": 12000 if count else 0},
            "expected_count": count,
        }
        for p, (instruction, order, count) in specs.items()
    }


def check(candidate, reference, allowed_profiles):
    value = candidate.model_dump(mode="json")
    template = templates()[candidate.profile]
    count = template["expected_count"]
    expected = {
        "type": "refund_ledger",
        "count": count,
        "total_fen": 12000 if count else 0,
        "order_id": "O-1001" if count else None,
    }
    issues = []
    if candidate.profile not in allowed_profiles:
        issues.append("profile_not_requested")
    if value["assertions"] != [expected]:
        issues.append("refund_ledger_contradicts_public_fixture")
    users = [t["user"].strip("。！! ") for t in value["turns"]]
    if count and users[-1] != "确认退款":
        issues.append("final_confirmation_not_supported")
    if candidate.profile == "refund_duplicate" and (
        len(users) < 3 or users[-2:] != ["确认退款"] * 2
    ):
        issues.append("duplicate_confirmation_turns_required")
    if candidate.profile == "refund_change" and len(users) < 3:
        issues.append("order_change_turns_required")
    # Do not infer arbitrary natural-language semantics; reviewers must verify each input.
    normalized = "".join(
        c
        for c in unicodedata.normalize(
            "NFKC", json.dumps(value["turns"], ensure_ascii=False)
        ).casefold()
        if c.isalnum()
    )
    return {
        "issues": sorted(set(issues)),
        "warnings": ["输入是否完整表达所选场景仍需人工审核；机器校验不等于语义已确认。"],
        "expected_fixture": template,
        "fixture": "refund-reference-v1",
        "text_fingerprint": canonical_hash({"fixture": "refund-reference-v1", "text": normalized}),
        "semantic_fingerprint": canonical_hash(
            {
                "fixture": "refund-reference-v1",
                "profile": candidate.profile,
                "expected": expected,
            }
        ),
    }
