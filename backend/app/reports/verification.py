"""Read-only repair eligibility. The same assessment gates the audited write."""

from app.reports.analysis import candidate_fingerprint, compare, latest_rows, verdict


def assess(defect, original, repaired, execution_id, fixed_version_id):
    first = next(
        item
        for item in original["executions"]
        if item["database_execution_id"] == defect["first_execution_id"]
    )
    peer = next(
        (item for item in repaired["executions"] if item["execution_id"] == execution_id), None
    )
    comparison = compare(original, repaired)
    source_rows = [
        row
        for row in latest_rows(first["assertions"])
        if candidate_fingerprint(original, first, row) == defect["fingerprint"]
        and row["verdict"] == "failed"
    ]
    matched = [
        row
        for row in latest_rows(peer["assertions"] if peer else [])
        if candidate_fingerprint(repaired, peer, row) == defect["fingerprint"]
        and row["verdict"] == "passed"
    ]
    checks = [
        ("rule_defect", defect["category"] == "rule_violation", "须为客观规则缺陷"),
        ("reviewed_fix", defect["status"] == "fixed", "须先确认缺陷并关联修复版本"),
        (
            "fixed_version",
            fixed_version_id == defect["fixed_version_id"] == repaired["target"]["version_id"]
            and fixed_version_id != original["target"]["version_id"],
            "复测须使用已关联的另一修复版本",
        ),
        ("run_completed", repaired["run"]["status"] == "completed", "复测运行须已完成"),
        ("execution_passed", bool(peer and peer["status"] == "passed"), "复测执行须已通过"),
        (
            "original_failure",
            bool(source_rows) and first["evidence_integrity"]["complete"],
            "原失败规则及证据须仍有效",
        ),
        (
            "evidence_complete",
            bool(peer and peer["evidence_integrity"]["complete"]),
            "复测证据须完整且与保存判定的输入一致",
        ),
        ("conditions", comparison["conditions_match"], "控制条件须一致且记录完整"),
        (
            "same_assertion_passed",
            bool(matched) and verdict(latest_rows(peer["assertions"] if peer else [])) == "passed",
            "相同预期与评价器的规则须通过，不能有其他最新规则失败",
        ),
    ]
    return {
        "schema": "defect-verification-v1",
        "defect_key": defect["defect_key"],
        "defect_revision_hash": defect["revision_hash"],
        "eligible": all(value for _, value, _ in checks),
        "checks": [
            {"id": key, "passed": bool(value), "label": label} for key, value, label in checks
        ],
        "original": {
            "run_id": original["run"]["run_id"],
            "execution_id": first["execution_id"],
            "version_id": original["target"]["version_id"],
            "assertions": [row["id"] for row in source_rows],
            "gateway_mode": first.get("gateway_mode"),
        },
        "verification": {
            "run_id": repaired["run"]["run_id"],
            "execution_id": execution_id,
            "version_id": repaired["target"]["version_id"],
            "assertions": [row["id"] for row in matched],
            "gateway_mode": peer.get("gateway_mode") if peer else None,
        },
        "comparison": comparison,
        "boundary": "只读核对，不确认修复；保存复核时服务端重新检查，不证明单因素因果或整体改善。",
    }
