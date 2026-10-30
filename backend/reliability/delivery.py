"""Finalize compact T17 evidence only after all independent checks pass."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime

from app.core.config import PROJECT_ROOT, Settings


def main():
    root = PROJECT_ROOT
    out = root / "docs/evidence/T17"

    def read(p):
        return json.loads(p.read_bytes())

    review = read(root / ".local/t17/latest-review.json")
    assert review["summary"]["checks_complete"]
    assert "260 passed" in (out / "backend-final.txt").read_text(encoding="utf-8")
    assert "5 passed" in (out / "frontend-regression.txt").read_text(encoding="utf-8")
    assert "1 passed" in (out / "ui/playwright.txt").read_text(encoding="utf-8")
    assert "All checks passed" in (out / "ruff-final.txt").read_text(encoding="utf-8")
    audit = read(out / "final-audit.json")
    assert audit["T16_manifest_unchanged"] and audit["old_ten_ledgers_unchanged"]
    assert audit["contracts_identical"] == 16
    secret = Settings().deepseek_api_key.get_secret_value()
    failures = out / "failures"
    failures.mkdir(exist_ok=True)
    for name, log in (
        ("offline-0c369bb90e6f", "t17-loads.log"),
        ("offline-7878191e120b", "t17-loads-second.log"),
        ("real-808f03f299d5", "t17-real-first.log"),
        ("offline-0f9c779d304d", "t17-final-free.log"),
    ):
        body = (root / ".local" / log).read_bytes()
        assert secret.encode() not in body
        target = failures / (name + ".txt")
        assert not target.exists()
        target.write_bytes(body)
    live = read(out / "real-8c1a9cd25d5e/live-calls.json")
    usage = [c["usage"] for c in live["calls"] if c["usage"]]
    completed_tokens = sum(u["total_tokens"] for u in usage)
    report = [
        "# T17 平台可靠性验证报告",
        "",
        "实施与验证完成，待团队检查；没有执行T18或提交推送。",
        "",
        "本轮改变应用连接事务隔离、复合证据读快照、容量拒绝后的重扫时机，并增加独立T17预算。冻结T16的源码、标签、失败与无改善结论保持，不把平台修复当作Agent语义提升。",
        "",
        "## 最终验证",
        "",
        "260后端0失败/错误/跳过；5项既有前端与1项实际桌面/390/320只读回放；类型/生产构建、ruff/216格式、94包离线锁与16份实际导出契约对照通过。",
        "",
        "| 并发上限 | 样本 | 峰值 | claim→finish P50/P95秒 | create→观察批次完成秒 |",
        "|---|---:|---:|---|---:|",
    ]
    for row in review["summary"]["loads"]:
        report.append(
            f"| {row['concurrency_cap']} | {row['samples']} | {row['observed_peak']} | "
            f"{row['claim_to_finish_p50_seconds']:.3f}/{row['claim_to_finish_p95_seconds']:.3f} | "
            f"{row['create_to_observe_batch_seconds']:.3f} |"
        )
    report += [
        "",
        "45条为相关离线控制，真实SQL/Redis/Linux任务；同机并行回归影响时延。每级另外投递15条重复消息，全部attempt1、0预约。不是生产容量、服务商吞吐或独立统计样本。",
        "",
        "另9类控制+3执行SSE共12执行：Redis保存后重启恢复原确认会话、Redis在途断开、旧Worker租约、派发前/在途/SQL提交后取消、元数据丢失、独立MySQL连接中断、发布失败重扫和API重启续看。最终SQL仅2笔，分别来自Redis恢复确认及提交后取消。",
        "",
        "SSE在5帧后断线并重启API，续看共54帧逐项等于SQL且无重复；错误游标400、跨run409、不可用410、他人404。实际慢读0.03秒/trace与ASGI故意慢send超时控制分别记录。",
        "",
        "## 真实模型与账本",
        "",
        "真实并发1/3/5共9个简短对话；另外2条在收到真实provider片段后取消/暂停。"
        f"11次请求，9个完整usage（合计{completed_tokens} token），2次未知。"
        "结算峰值费率估算0.007626美元，继续持有0.019570美元，总占用0.027196美元；"
        "未知不退款，84155 token_upper含预留，不能当实际总token或账单。",
        "",
        "独立上限1美元/60请求/每run0.10美元，生成器/裁判0。旧十账本逐字段不变，日常四付费开关false，付费Worker全部移除。真实目录初始session.json是无密钥免费启动快照，启用范围以live-binding/live-calls/live-summary为准。",
        "",
        "官方价格核对：[DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)，2026-10-30所列Flash峰值输入miss0.30/输出1.20美元每百万，与冻结估算一致。没有尝试通过大量请求触发服务商限流。",
        "",
        "## 失败与适用边界",
        "",
        "先保留REPEATABLE READ下两个独立run写缺失UUID的1213复现，再改应用写连接READ COMMITTED；"
        "同run显式锁、预约槽锁与唯一约束仍保留。证据复合读显式只读REPEATABLE READ，"
        "修复RC下高水位与事件来自不同时点的竞态。容量未认领时清除派发冷却，释放空位即可重扫。",
        "",
        "初始角色默认值、镜像知识不符被版本门拒绝；真实入口漏导出未发请求；OS暂停恰逢SQL持锁造成1205。全部失败原始归档、相邻清单与控制台日志保留。租约不能释放冻结活连接的SQL事务锁；需要恢复/终止自有进程后才能继续，调度器扫描报错/重扫并不等于自动解锁。最终旧Worker控制没有触发该超时，不撤回之前观察到的限制。",
        "",
        "新增限额回归发现旧开放授权兼容问题，已修复并通过全量；原失败日志保留。跨存储预留后SQL镜像异常保留Redis预留；丢失账本拒绝调用，原子60请求及每run金额边界另有真实Redis测试。",
        "",
        "## 保存与复核",
        "",
        f"最终免费原始目录：`{review['evidence']}`；"
        "真实目录：`docs/evidence/T17/real-8c1a9cd25d5e/`。"
        "每个实验队列只存一份压缩SQL/事件与artifact，provider原始调用单独一份ZIP，逐文件哈希已核对。"
        "8个队列包含失败与分段试跑，不混成统一成功率。",
        "",
        "主库41兼容版本（原40行不改）、123用例、4artifact，运行/预约/缺陷仍0；16表23触发器0006。13260历史证据/数据Git对象核对，789已知原有CRLF差异保留，T16清单原字节不改。旧应用源码以0f26016读取，不能用本轮源码对旧清单声称一致。",
        "",
        "团队只读入口、账号/步骤/预期、提前关闭与免费重建见`docs/acceptance/T17.md`。单份录屏`ui/t17-results-replay.webm`只是自动化结果回放，不是人工验收或作品配音演示。",
        "",
        "最终核对`final-audit.json`，原始诊断`failures/`，交付SHA清单`delivery-manifest.json`。没有清理旧.local或历史原始证据，没有新增C盘安装/独立WSL，也未再清MySQL日志。",
        "",
    ]
    report_path = out / "final-report.md"
    assert not report_path.exists()
    report_path.write_text("\n".join(report), encoding="utf-8")
    paths = set(p for p in out.rglob("*") if p.is_file())
    paths.update((root / "backend/reliability").glob("*.py"))
    for name in (
        ".gitattributes",
        "任务规划.md",
        "docs/acceptance/T17.md",
        "docs/architecture/platform-reliability.md",
        "backend/tests/test_reliability.py",
        "backend/tests/test_database.py",
        "backend/scripts/prepare_reliability.py",
        "backend/scripts/freeze_platform_version.py",
        "backend/scripts/authorize_executor_budget.py",
        "backend/scripts/verify_reliability_ui.py",
        "frontend/tests/reliability.spec.ts",
        "backend/app/db/engine.py",
        "backend/app/evidence/reader.py",
        "backend/app/executors/budget.py",
        "backend/app/executors/service.py",
    ):
        paths.add(root / name)
    paths.update((root / "docs/fixtures/T17").glob("*"))
    manifest = {
        "schema": "T17-delivery-sha256-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "base_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "files": {
            p.relative_to(root).as_posix(): {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "bytes": p.stat().st_size,
            }
            for p in sorted(paths)
        },
        "scope": "T17; team acceptance pending; no Git publication",
    }
    (out / "delivery-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "files": len(paths),
                "completed_usage_tokens": completed_tokens,
                "T17_evidence_bytes": sum(p.stat().st_size for p in out.rglob("*") if p.is_file()),
            }
        )
    )


if __name__ == "__main__":
    main()
