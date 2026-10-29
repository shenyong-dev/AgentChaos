"""Render an auditable interim or final report from an explicitly chosen analysis snapshot."""

import argparse
from collections import Counter
from pathlib import Path
from statistics import median, quantiles

from experiments.common import ROOT, read


def display(value):
    return "N/A" if value is None else str(value)


def render(data, source):
    rows = data["rows"]
    complete = len(rows) == 320
    lines = [
        "# T16 E2 串行正式实验统计",
        "",
        "状态："
        + (
            "320条执行已导出；仍须单独核对E1真人标签与最终验收。"
            if complete
            else "进行中，不能作最终效果结论。"
        ),
        "",
        f"分析来源：`{source}`；冻结包：`{data['dataset_hash']}`。",
        "每版本120个不同用例，关键20各补2次，合计160执行；两版本共320。重复观察不宣称统计独立。",
        "",
        "| 版本 | 计划 | 已观察 | 规则passed | failed | inconclusive | error | "
        "等待输入 | 取消 | 跳过 | 未执行 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for version, value in data["statistics"].items():
        counts = value["status_counts"]
        lines.append(
            f"| {version} | {value['planned_episodes']} | {value['observed_episodes']} | "
            + " | ".join(
                str(counts.get(k, 0))
                for k in (
                    "passed",
                    "failed",
                    "inconclusive",
                    "error",
                    "waiting_input",
                    "cancelled",
                    "skipped",
                    "not_executed",
                )
            )
            + " |"
        )
    lines += [
        "",
        "规则passed不是完整语义验收；未知、错误与等待输入不能计成功。分母始终保留计划160，不删失败。",
        "",
        "| 版本 | 已知token | 未知usage调用 | 已知高峰费率估算美元 | "
        "策略拒绝事件 | 观察时长中位秒 | P95秒 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for version, value in data["statistics"].items():
        group = [r for r in rows if r["episode"]["version"] == version]
        durations = [
            r["observation_elapsed_seconds"]
            for r in group
            if r["observation_elapsed_seconds"] is not None
        ]
        p95 = quantiles(durations, n=100, method="inclusive")[94] if len(durations) > 1 else None
        lines.append(
            f"| {version} | {value['known_tokens']} | {value['unknown_usage_calls']} | "
            f"{value['known_peak_estimate_microusd'] / 1_000_000:.6f} | "
            f"{sum(r['single_call_policy_rejections'] for r in group)} | "
            f"{display(round(median(durations), 3) if durations else None)} | "
            f"{display(round(p95, 3) if p95 is not None else None)} |"
        )
    pairs = data["paired_conditions"]
    transitions = Counter((p["V0_status"], p["V1_status"]) for p in pairs)
    lines += [
        "",
        "费用是冻结费率估算、不是账单；此表仅串行正式执行，诊断试跑与裁判费用另在同一T16账本中。",
        "观察时长为claim至导出，包含模型/工具/SQL，不是纯模型延迟或平台负载测试。",
        f"已配对{len(pairs)}/160；"
        f"声明条件不一致{sum(not p['same_declared_conditions'] for p in pairs)}对。",
        "",
        "| V0状态 | V1状态 | 对数 |",
        "|---|---|---:|",
    ]
    lines += [f"| {a} | {b} | {n} |" for (a, b), n in sorted(transitions.items())]
    lines += [
        "",
        f"另保留诊断队列{len(data['diagnostic_pilot_rows'])}条观察，全部排除于正式320分母，包括成功和失败。",
        "分区、类别、关键场景的完整计划/未执行分母见分析JSON的strata；每一行source指向原始run/events/observations/provider。",
        "",
        "V1只增加单次多个工具调用的整组拒绝策略。若自然策略触发为0或无改善，不能宣称已验证修复有效。",
        "固定模型别名不能钉死服务端版本；平台seed不保证模型确定性。全局串行且按case/repeat交替，不能消除时间漂移。",
        "原生Windows执行器使用冻结函数；本轮没有新增Linux双Worker或平台负载结论。",
        "E1独立人工金标准与回答/增强方法比较另存，不用规则结果生成真人标签。",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    args = parser.parse_args()
    source = args.analysis.resolve()
    assert source.is_relative_to((ROOT / "docs/evidence/T16/analyses").resolve())
    text = render(read(source), source.relative_to(ROOT).as_posix())
    output = source.with_name("E2-report.md")
    with output.open("x", encoding="utf-8") as file:
        file.write(text)
    print(output)


if __name__ == "__main__":
    main()
