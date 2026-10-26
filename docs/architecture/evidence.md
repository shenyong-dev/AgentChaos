# T06 持久事件与只读证据

MySQL保存已提交事件和运行/预约事实。Redis检查点用于继续执行；事件查询不读取检查点来重演工具。T06不实现故障注入、断言评价、正式用例审核和T14完整工作台。

## 事件身份与提交顺序

`trace-event.schema.json`为严格的v1.1判别联合，payload.kind必须与type一致；原v1.0保留在`trace-event-v1.0.schema.json`。历史旧payload显式输出1.0，不伪装成完整的新轨迹。只有新冻结manifest.trace_schema=1.1的运行才启用记录器。

迁移0003保留16表。ac_trace_events新增run_id和run_seq_no；run级事件execution_id/attempt在外部协议为空，执行级事件绑定真实execution与attempt。原execution/attempt/seq唯一约束及父事件复合关联保留，不允许跨执行或attempt引用；run级父事件由触发器核对归属。

写入先锁run再锁execution。BEFORE INSERT触发器锁同run的event_seq，分配连续run_seq_no，计数与事件在同一事务提交；回滚不推进序列。不同run可以独立推进；全局自增id只作内部主键，不能作为提交游标。状态收尾与对应生命周期事件同一SQL事务。事件和artifact清单继续禁止普通UPDATE/DELETE；DDL管理员仍可改变保护，不宣称外部防篡改审计。

EvidenceRecorder覆盖生命周期、节点进入/离开/错误、模型请求/返回/错误、工具请求/结果、检索、确认、状态差异、上下文压缩及真实检查点恢复。重复节点进入保留node_visit；工具结果用实际tool_call_id关联请求事件。模型保存请求配置/返回模型、usage、耗时、无自动重试及原始返回；模型流只汇聚为返回证据，不逐token写事实表。输入UUID、确认修订、实际业务前后状态可核对。

记录器仍遵守原租约。唯一例外是已有调用票据所属attempt的迟到模型结果：可追加late_observation及结算证据，不能改回运行状态或执行工具。Worker硬退出、提供方结果无法获得时保留model.started和pending_model_observations；不捏造失败返回，客户端显示待核对并定期重连。

## 脱敏与大段证据

序列化前替换本地已知凭据及敏感字段、sk-/Bearer模式。超过24KiB的payload转存忽略目录`.local/evidence/<run_uuid>/<event_uuid>.json`，事实行保留摘要、artifact ID/字节数/SHA256和字段引用；单文件受8MiB上限约束。哈希在脱敏后计算。真实工具原结果在模型可见裁剪前采集，二者分别记录。

数据库提交后才能经artifact清单读取文件。下载须同run所有者、路径位于受控目录、大小及SHA256匹配。事件读取也校验payload_hash。文件系统与SQL不是分布式事务：回滚可能留下无法经API访问的孤立文件；不自动删除既有证据，后续留存策略另行处理。哈希不代替脱敏或权限。

## 只读API

沿用Basic操作员认证，跨所有者返回404。凭据只留后端本地配置和浏览器当前页内存；本地同源开发使用，公网部署需TLS和后续完整认证。

| GET路径，前缀 `/api/v1` | 返回 |
|---|---|
| `/runs/{id}/snapshot` | 一致SQL快照：状态、冻结清单、预算、执行及高水位cursor、最近100条之前的tail_cursor |
| `/runs/{id}/events?cursor=...&limit=100` | run_seq升序分页items(cursor,event)、next_cursor、has_more、高水位和待返回模型数量 |
| `/executions/{id}`及`/events` | 归属检查后的执行快照和按执行过滤的运行游标分页；过滤结果不承诺run_seq连续 |
| `/runs/{id}/artifacts/{artifact_id}` | 校验归属/文件哈希后返回JSON，ETag为SHA256 |
| `/runs/{id}/events/stream` | 已持久事件的原生SSE；不创建模型请求或工具调用 |

游标为`v1:<run_uuid>:<run_seq>:<event_uuid>`，起点末两项为0:0。服务端同时核对序列和事件UUID，不接受其他run或不存在的边界。格式400、run不匹配409、边界不可续传410并要求重取快照；查询cursor与Last-Event-ID不一致400。鉴权/初始游标检查发生在SSE响应头发送前。

## SSE与页面

使用锁定FastAPI 0.162.0原生EventSourceResponse，框架15秒心跳、X-Accel-Buffering:no；应用统一Cache-Control:no-store。同源代理需关闭缓冲，保留Authorization/Last-Event-ID和流式flush；代理idle timeout应大于心跳间隔。每次连接默认30秒轮换、有限批读取；慢发送默认5秒期限，中断后按客户端最后完整解析ID重连。框架有限队列避免无限缓存。

`stream.ready`、`stream.rotate`、`stream.end`、`stream.error`和`stream.reset`为无ID的连接控制帧；只有`trace`帧带持久游标ID。run终止且无待返回模型、所有已提交事件发送完后才end。积压超过1000条返回reset/snapshot_required，不静默丢弃事件。错误和轮换均由前端显式处理。

`/runs`页面从快照tail_cursor载入最近事件，也能从头分页。当前窗口最多500条，满后暂停并提示分段阅读；保留位置，不强制滚到底部。连接通过fetch流携带认证，处理UTF-8分块、多行data及半帧；完整校验身份/顺序后才推进cursor，按event_id去重，发现缺口停止推进。断线从最后已应用ID重连，5次连续失败后保留证据并显示中断。刷新重新读取持久记录，不触发执行。界面inconclusive明确表示尚未评价。

## T06模型授权

用户对本项agent明确“同意，你随用，无上限”。新账本`T06-20261026-agent-open`的limit=-1表示这一明确授权；0仍表示禁止。需同时开启EXECUTOR_AGENT_BUDGET_UNLIMITED及匹配授权ID，不把金额0解释为无限。独立保留原T04/T05账本；case_generator/judge仍0。每运行金额、请求/步骤/token/时限限制继续有效，逐调用票据、已知费用估算和未知预留继续记录。账本不设TTL，缺失时拒绝调用，不能通过重启重置。

显式验收：先构建agentchaos-t06:dev，再执行`backend/scripts/verify_evidence.py --real-model`。使用临时数据库、限定SQL用户、独立队列和现有D盘Linux引擎；导出事件、调用、artifact及页面证据后清理自己创建的容器/用户/库，会话和临时连接配置。此命令会产生真实付费请求，必须已有对应授权。结果与限制见[T06验收](../acceptance/T06.md)。
