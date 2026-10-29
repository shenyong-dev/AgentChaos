"""Render the fixed T13 candidate for genuine human review; never approves it."""

# ruff: noqa: E501 -- authored review text preserves full concrete instructions.

import json

from app.core.config import PROJECT_ROOT
from app.datasets.catalog import audit, experiment_plan, load_catalog


def main():
    value = load_catalog()
    stats, plan = audit(value), experiment_plan(value)
    dest = PROJECT_ROOT / "docs/fixtures/T13"
    dest.mkdir(parents=True, exist_ok=True)
    lines = [
        "# T13 数据集与实验规模审核提案（尚未人工批准）",
        "",
        f"数据包hash：`{value['package_hash']}`。固定参考时钟：`{value['reference_clock']}`。",
        "",
        "请团队逐条核对下列120条输入、空初始状态与独立预期，重点交叉检查关键20条。确认的是测试预期与计划，不是Agent已经通过。"
        "本提案由助手编写，不伪称已有真人审核或付费生成。",
        "",
        "场地夹具：A101=20人/09—21点，B201=40人/09—21点，C301=80人/09—18点；每条全新actor绑定scope/session，初始预约和草稿均为空。"
        "预约30—120分钟，按半小时对齐，时间在固定参考时钟之后且不超过7天。每条知识profile见datasets/v1/knowledge.json；规则均为自建实验。",
        "",
        "60 dev / 30 calibration / 30 holdout，77个手工归类的家族标签。标签数不能当作统计独立家族证明。"
        "相同机制、数字/同义改写、故障阶段和触发时序需整体归类；分区内可以有不同边界实例，但不以重复运行扩数。"
        "规范化文本+知识+故障精确重复为0，语义近似仍需团队审核，不能由这个检查宣称完全无污染。",
        "",
        "作者助手为构建/审计看过所有条目；没有送给被测Agent、case_generator或judge，也不据此调Prompt/检索器。"
        "dev包含既有开发机制。T12历史30条校准不能冒充本次30条calibration，未来回答标签也不能预填。",
        "",
        "## 覆盖与关键条目",
        "",
        "| 类别 | dev | calibration | holdout | 合计 |",
        "|---|---:|---:|---:|---:|",
    ]
    for cat, row in stats["coverage"].items():
        lines.append(
            f"| {cat} | {row['dev']} | {row['calibration']} | {row['holdout']} | {sum(row.values())} |"
        )
    lines += [
        "",
        "关键20：" + "、".join(value["critical_cases"]),
        "",
        "## 正式实验规划（本轮0调用）",
        "",
        "E2：V0/V1各完整120条一次；关键20条各额外重复2次，合计每版本160、两版本320计划执行。"
        "同一case/repeat的seed为13028/13029/13030，两版本保持模型、知识、工具、初始夹具、故障与限额一致。"
        "一次恢复attempt不算新增实验重复。V1当前未登记，不能把V0复制为V1或声称已比较。",
        "",
        "E1：复用V0关键20×3的60条冻结轨迹，不再重跑Agent。两种方法复用同一份仅回答视图judge意见，增强组追加轨迹/SQL规则。"
        "仅回答视图不同于T12完整证据视图，正式质量分析前须补30条实际独立标注与该视图校准；另60条真实轨迹需要独立人工金标准。"
        "因此judge规划共90次；生成器0。未触发的故障单列，不当作容错成功。",
        "",
        f"计划占用上限：agent {plan['role_caps_microusd']['agent'] / 1e6:.2f}美元；judge 1.35美元；生成器0；"
        f"合计**{plan['total_planning_cap_microusd'] / 1e6:.2f}美元**。这是320次执行的逐条预算上限汇总，**不是预期实付费用或当前价格核实**。"
        "普通每次0.15美元、长对话每次0.50美元；每条还保留模型调用、token、步骤与时限限制。"
        "本次确认只冻结计划，不创建新付费账本、不启用调用，正式执行须在获准的后续任务按实际模型/费用条件启动。",
        "",
        "## 120条逐条输入与预期",
        "",
    ]
    critical_lines = [
        "# T13 关键20条交叉检查",
        "",
        f"数据包：`{value['package_hash']}`。完整前提/预算见review-proposal.md。",
        "",
    ]
    for c in value["cases"]:
        d, e = c["dsl"], c["independent_expectation"]
        block = [
            f"### {c['id']} {c['title']}" + (" ★关键" if c["critical"] else ""),
            "",
            f"类别`{d['category']}`；分区`{d['dataset_split']}`；家族`{d['family_key']}`；知识`{c['knowledge_profile']}`。",
            "",
            "输入（按次序；不临时编造澄清或确认）：",
            "",
        ]
        block += [f"{i}. {t['user']}" for i, t in enumerate(d["turns"], 1)]
        block += [
            "",
            f"允许的最终有效预约数量：**{e['active_booking_counts']}**。依据：{e['reason']}",
            "",
            "允许的写入参数：`"
            + json.dumps(e["permitted_booking_facts"], ensure_ascii=False)
            + "`。数量允许0/1的故障例，0需如实降级；1须符合这些参数，不能把未完成强迫判失败或强迫判成功。",
            "",
        ]
        if c["fault"]:
            block += [
                "故障冻结：`"
                + json.dumps(c["fault"], ensure_ascii=False)
                + "`。需在实际事件中证明触发。",
                "",
            ]
        if e["manual_checks"]:
            block += ["人工核对：" + "；".join(e["manual_checks"]), ""]
        if "retrieval_manual_annotation" in e:
            block += [
                "检索独立预期：`"
                + json.dumps(e["retrieval_manual_annotation"], ensure_ascii=False)
                + "`",
                "",
            ]
        block += [
            f"单次agent占用上限：{c['agent_cap_microusd'] / 1e6:.2f}美元；调用{c['limits']['max_model_calls']}、token {c['limits']['max_tokens']}、时限{c['limits']['timeout_seconds']}秒。",
            "",
        ]
        lines += block
        if c["critical"]:
            critical_lines += block
    (dest / "review-proposal.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (dest / "critical-20.md").write_text("\n".join(critical_lines) + "\n", encoding="utf-8")
    (dest / "experiment-plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (PROJECT_ROOT / "docs/evidence/T13/static-audit.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Reviewable 120 cases / critical 20 / planning cap {plan['total_planning_cap_microusd'] / 1e6:.2f} USD; no approval or calls."
    )


if __name__ == "__main__":
    main()
