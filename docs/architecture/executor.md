# T05—T09 持久批量执行契约

2026-10-28更新。运行/执行状态以MySQL为准，Celery只负责至少一次投递。仍16张领域表、23个触发器，迁移20261026_0003；[事件/SSE](evidence.md)、[T07故障](faults.md)及[T08独立规则评价](evaluation.md)已接入，T11正式审核尚未实施。T04—T07原授权账本保持；T08无新模型调用且日常付费开关关闭。

T09 Worker按manifest中的确切知识修订列表构建索引并核对哈希，不能缺修订后偷偷回退；可信服务构造参数可选择开发隔离资料，HTTP CreateRun仍不接受客户端知识列表。独立T09 agent授权T09-20261028-agent-open不设金额总上限，每运行仍最多1美元，逐执行100次调用/500步骤/2M token/900秒及单轮限制保留。实际边界、开发样例及结果见[多轮契约](dialogue-boundaries.md)。

## API

沿用HTTP Basic操作员认证和统一Envelope；身份、scope、session、租约不能由JSON指定。

| 方法/路径（前缀 `/api/v1/runs`） | 行为 |
|---|---|
| POST 空后缀 | 客户端request_id UUID幂等创建并持久化queued，返回201；不等待整批完成 |
| GET `/{run_id}` | 读取冻结manifest/预算及各execution的状态、attempt、usage、前后业务事实与最终输出 |
| POST `/{run_id}/cancel` | 合作式取消；立即撤销未终止执行的租约，保留已经提交的预约 |
| POST `/{run_id}/dispatch` | 核对归属后触发限频补投；投递失败登记在timing.dispatch_error，任务仍保留 |
| POST `/{run_id}/executions/{execution_id}/input` | 只接受waiting_input的request_id、expected_revision和原始text；不自动生成确认 |
| POST `/{run_id}/executions/{execution_id}/recover` | 显式恢复已失败的原检查点节点，需request_id/expected_revision；不建立新scope/session |

所有执行明细包含在运行读取结果中。跨身份返回404。请求未知字段400、未批准用例/哈希或版本不一致409、超过本地授权预算403。运行创建不提供客户端审核入口。

创建示例（ID取实际已登记版本与已审核用例，不能直接把示例ID当成种子数据）：

```json
{
  "request_id": "c85431b5-d892-43db-83bb-0f09874ce48a",
  "target_version_id": 4,
  "case_revision_ids": [1, 2],
  "repetitions": 1,
  "seed": 26,
  "limits": {
    "concurrency": 1, "timeout_seconds": 300,
    "max_steps": 100, "max_model_calls": 20, "max_tokens": 500000
  },
  "budgets": {"agent_microusd": 100000, "case_generator_microusd": 0, "judge_microusd": 0}
}
```

同一request_id/同一请求返回原运行；参数不同409。当前只执行booking-default-v1夹具，先验证用例批准字段、内容哈希和DSL，再冻结用例修订/审核记录、目标代码与知识/工具、三角色参数/超时/输出限制、价格、种子、计划重复及执行限制。当前不向DeepSeek发送未经验证的seed参数，seed不保证模型确定性。

## 生命周期与恢复

MySQL提交运行和执行后，独立派发器每3秒扫描queued或失联running。每项至少间隔15秒再投递；发布前持久记录dispatch标识，发布成功后登记结果。进程在任一边界崩溃，下一扫描可补投。JSON消息只包含execution UUID，不包含凭据或客户端身份。

认领串行核对数据库中有效租约数量和冻结并发上限；一个执行同一时刻只有一个有效lease_token。默认租约45秒、心跳约11秒，认领和业务保护使用MySQL UTC_TIMESTAMP(6)，不相信Worker本机时钟。锁顺序固定为run→execution。事务执行工具前后都检查token/期限/取消/scope/session，过期时回滚；LangGraph检查点和会话写入也受同一锁顺序保护，新租约不能被旧Worker覆盖。

每个execution分配唯一test:scope和session。初始化只确保固定场地存在，不清空已有预约。基础设施恢复追加attempt_history，计划repeat_no保持不变。每轮输入在调用前先持久化inflight（确定性request UUID、文本、原修订）；完整缓存回执可以安全重读。未形成完整回执的在途输入不自动重放，标记error/recovery_uncertain，保留原数据供核对。

显式recover只重试原图的失败节点，不重发整个脚本，不重置时限、额度或业务幂等键；最多三个attempt允许显式节点恢复。原检查点缺失、已完成/中断而无法证明失败节点、期限已到等情况继续拒绝恢复，需要先恢复经过核对的原始存储或另建明确的新运行。不能把清空数据作为恢复。自动基础设施认领也受原墙钟期限约束。

脚本耗尽但图等待确认时为waiting_input，清除租约并返回Worker；合法新输入使其重新queued，仍沿用同一session/attempt。关闭页面不取消后台任务。取消会阻止下一模型/工具/检查点写入；已经进入的模型请求可能产生未知费用，保守占用预留。已经提交的预约保留，执行取消不自动取消业务预约。

等待输入也遵循原墙钟期限。派发器每轮扫描时收尾已逾期的queued/running/waiting_input，写execution_timeout并撤销租约；无需人再次发送消息，不产生新模型调用。业务前后快照与run汇总采用锁下当前读，避免REPEATABLE READ旧快照漏掉刚完成的执行或业务事实。

T08之前，脚本完成统一inconclusive/evaluation_not_implemented。run.completed仅表示所有执行已终止，可以包含error或inconclusive，不能解读为智能体通过。工具错误留在实际工具结果/对话轨迹中，模型服务错误、执行限制、控制拒绝和平台错误分开记录。单次模型/工具与整体调用上限共同限制重试，不使用Celery自动重试套娃。

T08新运行冻结评价器代码/Schema指纹、DSL版本和task_goal；事件1.3，SQL前后快照记录实际room_code。正常完成在既有run→execution锁和事务内加载持久证据、保存不可变断言并记录assertion.completed/evaluation.completed，再按passed/failed/inconclusive/error结束；runner返回实际评价状态。等待输入仍不评价，取消/运行错误保留原状态，显式重评结果单列，不篡改旧1.0/1.1/1.2运行。任务达成与可靠性判定分开，na不当成passed。

T05/T06/T07历史验收脚本已兼容当前正常结束的passed/failed状态，未在T08重新执行付费矩阵。T08的真实存储/离线模型验证及旧真实轨迹只读复核不代替新的Linux双Worker实验。

## 预算与证据

单位为整数microUSD。T05授权T05-20261026-agent-1累计1美元，case_generator/judge为0。与T04账本独立，重启、重新运行验收脚本或换临时数据库都不新增额度。

授权全局、单run、单execution和调用票据使用不设TTL的Redis持久协调账本，一次Lua校验金额、调用数和保守token上界并预留。全局key不随测试namespace切换；跨run竞争仍共享批准上限。之后在发送模型请求前把票据写入MySQL usage_json，任一步失败不发请求。Redis成功而SQL失败时保留预留，不能自动退回；请求取消或usage缺失同样保留。返回真实usage后幂等结算，并镜像到运行summary_json和执行usage_json。冻结budget_json只保存配置，不写累计消耗。

输入token预留为请求UTF-8字节数×2+8192，输出使用配置上限；已知usage结算后替换保守token占用，未知继续保留。价格按2026-10-26核对的高峰cache-miss输入0.30美元/百万、输出1.20美元/百万估算，不是账单。[DeepSeek官方价格](https://api-docs.deepseek.com/quick_start/pricing/)

Worker不初始化账本。经明确授权后仅本地运行一次：

```powershell
backend\.venv\Scripts\python.exe backend/scripts/authorize_executor_budget.py --authorization T05-20261026-agent-1
```

初始化标记保存在忽略的.local/t05中；重复执行只读取原账本，Redis账本丢失且本地已有标记时拒绝重建。发生存储丢失/回滚时必须依据保留的调用票据和原始证据核对恢复，不能把旧快照当新额度。

MySQL为运行状态和预约事实来源；Redis队列/检查点/预算协调数据需按部署说明持久化。两种存储并非分布式事务，跨存储失败采用保守停机和保留预留。开发临时库清理前导出全部运行与真实调用证据；全局授权账本继续保留。

## Linux启动与验收

使用现有Docker Desktop Linux引擎及D盘数据盘，不创建独立WSL，不在Windows运行Celery Worker。容器非root运行，源码布局包含知识库、迁移及协议证据。生产Compose的worker profile含独立Worker和dispatcher；已有本机数据库开发配置使用host.docker.internal和忽略的.env，不能把Compose的mysql/redis主机名用于本机服务。

本机Docker原AppData目录存在失效挂载点，直接启动桌面入口会失败。本轮以子进程APPDATA/LOCALAPPDATA指向项目D盘.local/t05/docker-profile，继续复用安装器指定的既有D盘数据盘；不修改全局用户目录。`scripts/start-linux-engine.ps1`封装现有引擎复用/启动和D盘检查。DockerCLI对公共镜像使用项目独立的空auths配置，避免原credential helper写失效目录；没有复制登录凭据。脚本不修复系统挂载点，重启后需按该入口检查引擎。

受控开发验收：`backend/.venv/Scripts/python.exe backend/scripts/verify_executor.py --real-model`；`--controls-only`只验证暂停/旧租约/显式恢复、取消和预算终止，不重复10条批次。需要本地已批准开关、账本、现有Linux引擎和已构建agentchaos-t05:dev镜像，仍消耗同一T05授权。脚本创建临时库/仅限该库的临时用户和独立队列，结束后清理自己创建的容器、用户、会话及数据库，保留预算与导出证据。

测试用例批准字段为明确标注的开发夹具，不代表团队人工审核或正式数据集；验收记录不把其模型结果写成通过。Celery预取1、acks_late和worker_lost重投仅提供运输语义，有效执行防重由数据库认领和工具幂等负责。[Celery任务语义](https://docs.celeryq.dev/en/stable/userguide/tasks.html)、[Redis运输与visibility_timeout](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)、[MySQL锁定读取](https://dev.mysql.com/doc/refman/8.0/en/innodb-locking-reads.html)

## T07补充，2026-10-27

创建run可选fault_profile_id；服务端冻结不可变配置/哈希，null为控制组。不传新字段的历史幂等请求哈希保持兼容。执行器仅为所选run创建FaultController，使用既有租约控制器/事件记录器；触发计数/回执在execution.timing_json中保留，恢复不重置。新事件版本1.2，原1.0/1.1仍读取，详见[故障契约](faults.md)。

T07另获明确agent无金额总上限授权，独立T07-20261027-agent-open；必须匹配授权ID、unlimited标志及已初始化持久账本。T04/T05/T06账本不修改，0仍表示未授权，case_generator/judge0。全球账本保留旧前缀ac:t05是兼容既有存储，并不随lab namespace变化；初始化标记亦保留，不迁移/删除以制造额度。

verify_faults.py明确要求--offline或--real-model。前者模型替身但SQL/Redis/工具真实，--review可保留临时团队UI且付费开关关闭；后者使用agentchaos-t07:dev、真实Linux双Worker/API/dispatcher及DeepSeek。两种模式分别导出并标注。日常团队检查入口、调用费用、保留失败和清理边界见[T07验收](../acceptance/T07.md)。start-linux-engine.ps1的启动分支在本轮实际验证，已修复Windows PowerShell5.1将探测stderr当终止异常的问题。
