# T16 正式实验工具与恢复

2026-10-29最终状态：320条正式结果、C/G1/G2共90条真人原标签及90次裁判已完成，付费队列已停止。以下启动命令仅为历史操作/恢复说明，不作为继续付费指令；当前验收仅需恢复免费视图或读取导出。最新指标与已知问题见`docs/evidence/T16/final-report.md`，团队验收见`docs/acceptance/T16.md`。

T16工具在`backend/experiments/`，不替代冻结被测程序。V0使用T13归档、V1使用获准的单调用候选归档，各12知识配置。执行时PYTHONPATH先指向对应`.local/t16/formal-v0/backend`或`formal-v1/backend`，再指向当前backend；只注入独立预算适配器，原执行函数源码哈希存于每份plan。

## 环境与授权

正式隔离库名和不含密码的来源见`docs/evidence/T16/session.json`。私有连接保存在`.local/t16/session-private.json`；不上传、不打印。三个角色分离，agent上限54.30美元、judge1.35美元、生成器0；旧8账本及初始化标记保留，不能删除或重建额度。根`.env`四付费开关保持false，真实调用只由本项受控子进程开放。

`setup`只允许首次初始化，已有私有会话时禁止再次运行。`preflight`记录的是首次调用前快照，不能在已消费后冒充重新初始化结果。`budget`原子预留与原结算/未知处理兼容，正式与诊断队列都消耗同一授权。

## 执行与恢复

从项目根目录，用既有`backend/.venv/Scripts/python.exe`，设置`PYTHONPATH=D:/AgentChaos/backend`、`TEMP/TMP=D:/AgentChaos/.cache/tmp`。正式串行命令：

```powershell
python -m experiments.launch --phase remaining --resume
```

仅在上一监督进程已结束且核对监听/进程归属后恢复。它核对原320条计划，跳过已有outcome，不重跑成功或失败；存在无outcome目录时立即停止，要求审计，不隐式重新付费。`.local/t16/stop`在下一条边界停止；C盘低于1.5GB也停止。不能通过清账本、删目录或扩预算绕过。

首次并行35条诊断观察与正式320分开；修订理由见`docs/fixtures/T16/infrastructure-amendment.md`。正式发生基础设施错误也保留在分母，不替换为成功。

## 真人标注与裁判

`python -m experiments.review_session`提供有时限的API18016/UI5183；子进程不带模型密钥，四付费开关关闭。关闭视图只停止自身进程，不删正式库、证据、预算。精确入口/到期/stop_file见`.local/t16/latest-review.json`。

`python -m experiments.labels --kind gold --gold-batch G1`在本批30条轨迹齐备时建立G1，G2同理，保存来源映射/观察SHA。分批只是让真人标注与剩余队列并行：在任何G标签/裁判产生前，已冻结按case_key升序、repeat1..3的60条分组，前30为G1、后30为G2，见`gold-batch-assignment.json`；不能按结果挑选。每条三项真人选择，不预填；独立封存全部60条后，才允许`python -m experiments.judge --batch G1`及G2。C批30条校准独立保留，不能替代60条金标准。标注细则与冻结组合规则见`docs/fixtures/T16/human-label-guide.md`和`E1-protocol.md`。裁判输入仅实际用户输入与最终回答，不含SQL、独立预期或标签；无自动补修或重试。

## 只读分析

`python -m experiments.collect`生成带时间戳的E2 JSON/CSV，含计划分母、分区/类别、未知、试跑及每对条件检查。`python -m experiments.report <该次E2.json>`生成对应Markdown。未完成320条时明确标进行中。`python -m experiments.e1`要求真实封存60标签，输出三方法比较与辅助一致率；缺意见保持未知，不伪造分数。

`python -m experiments.export_assertions`只读追加持久断言侧档，含expected/actual/证据引用；不修改冻结导出器、不重新评价。`python -m experiments.audit --allow-active`核对旧主库/8账本/4209文件、冻结源码、正式执行器哈希、事件连续性及provider SHA；最终在执行停止后去掉allow-active再审计。早期试跑未复制provider的原始调用已用`archive_pilot`追加231份同字节归档，不改原导出；它不是新模型调用。

分析脚本可更新展示与审计，但执行相关`common.py/budget.py/episodes.py`以及两个冻结源码目录在正式队列期间保持不变。所有来源、输入、原始响应、错误与未知usage保留。MySQL数据仍在C盘；已获准清理的20份关闭binlog仅此次精确范围，D盘备份在`.local/t16/mysql-binlogs/`含其他库历史，不得上传。后续空间清理另按明确授权处理。

## 最终只读复核

`python -m experiments.accounting`比对SQL预算票据与Redis计数，另存带时间戳快照，不重置预算。`python -m experiments.audit`核对旧文件/旧账本/主库、冻结执行器与全部Agent导出，要求无running执行；等待输入保持等待，不转通过。

`experiments.delivery`已一次性生成`delivery-audit.json`，核对90个实际盲评输入/原始调用与SQL、三份原封存、2527个run artifact；重复运行遇现有文件会拒绝覆盖。`--manifest-only`在文档收尾后生成公开文件SHA清单，同样拒绝覆盖。复查现有清单只读计算各path的SHA256即可，私有连接/备份不进入清单。

`experiments.sensitivity`仅将G1用户要求的助手参考与原独立标签分别计算，不能替换E1原标签。裁判错误诊断、辅助建议及失败复现是新增解释材料，不修改冻结的提示词/分析口径/正式样本。当前收费调用已达到本批90次裁判上限，余额不表示可以新开调用。
