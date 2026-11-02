# T17D 预约 Agent 交互协议

交互调试复用 AgentRuntime、LangGraph 检查点、工具白名单、字段来源验证和原预约确认协议。它是被测预约 Agent 的开发入口，不是 T17E 的平台助手；不生成独立金标准或修改历史实验结果。

## 信任与隔离

- HTTP Basic 身份沿用既有 operator 认证。reviewer 不得操作预约；所有会话、历史、轨迹和回执均先校验 owner。
- 会话 namespace 绑定实际数据库连接读取的 `@@server_uuid / DATABASE()` 的哈希；不从客户端或未验证的配置推断。session UUID 由环境绑定、用户与创建 request_id 派生，同一请求重发返回同一会话。
- 服务端创建独立 `test:interactive:<session_uuid_hex>` 场地范围，只初始化该范围的实验房间；不读取学校真实预约。scope、owner、参考时钟、配置哈希、角色均不能从请求体指定。
- 每个会话绑定不可变对象版本。源码、Prompt、依赖锁变化会清理编译图缓存；旧会话明确拒绝，不自动切换版本。最多缓存 64 个图，不影响持久检查点保留。
- 现有 `/demo` 协议与新交互 namespace 分离。数据库相同用户编号、相同版本也不能跨实际数据库读取会话。

## API

统一 `/api/v1/interactive` 前缀，保留 Envelope、请求标识、无缓存、脱敏异常规则。请求 Schema 见 `interactive-create.schema.json`，其他输入复用 DemoMessage/DemoResume/DemoRetry。

| 方法与路径 | 用途 |
| --- | --- |
| GET `/access` | 当前版本、配置哈希、环境绑定标识、模型调用条件及限额，不调用模型 |
| POST `/sessions` | `{request_id,version_id}`；幂等创建自有实验范围，不调用模型 |
| GET `/sessions/{id}` | 当前检查点、字段来源、最近 40 条审计、独立带时间的 SQL 观察、原未知请求键查证及会话用量 |
| GET `/sessions/{id}/history?cursor=0&limit=50` | 按持久历史分页，limit 最大 100；前端不丢原有对话 |
| GET `/sessions/{id}/trace?cursor=0&limit=50` | 完整审计轨迹按需分页，工具参数、结果和规则引用保持原值 |
| POST `/sessions/{id}/messages` | request_id、expected_revision、text；服务端验证来源与会话版本 |
| POST `/sessions/{id}/resume` | 绑定 draft_id/hash、confirmation_revision 和 expected_revision 的 confirm/reject |
| POST `/sessions/{id}/retry` | 用户显式恢复失败节点，不自动换输入或重跑已完成动作 |
| GET `/sessions/{id}/receipts/{request_id}` | completed/failed/processing/not_registered，不再发模型请求或预约写入 |

前端以完整 HTTP 轮次返回显示回答，没有 token SSE，不做逐字动画。提示模型处理与工具结果尚未返回时，不能展示预设的成功。

## 请求回执与副作用

业务确认仍由原工具检查草稿、有效期、授权和幂等键。新增外围操作锁与持久回执，输入开始前先保存 payload_hash、revision 和请求标识。相同 ID 不同参数拒绝；已经完成的原请求即使付费条件关闭也可以读取和重放响应，不增加模型调用。

连接中断不等于失败。浏览器当前内存保留原 body，同页允许显式重发完全相同的请求；sessionStorage 只保存请求标识和无文本的创建参数，不保存消息正文或凭据。刷新后重新登录，先读原会话和回执，不自动创建或重新预约。服务端没有确定回执且无法从 runtime 缓存恢复时保留未决状态，阻止新输入；需要维护者核对，不擅自释放未知操作。

SQL 观察按本会话 scope/actor 读取，时间另列，失败时 booking_count=null，不能写成 0。草稿状态、模型回答和 SQL 预约数独立展示。未知提交只用原 request_key 查证；GET 不重发确认。对原键的查询不能因为回答声称成功而补造结果。

## 预算与验证

2026-11-02 用户明确批准 T17D agent 独立 1 美元／120 请求，每会话 0.10 美元／20 请求。仅 `T17D-20261102-agent-1` 匹配配置时开放；全局、会话和请求票据在 Redis Lua 原子预留/结算。总账本缺失即拒绝，没有隐式初始化或退款；未知 usage 保留预留。初始化命令使用独立标记与原子条件，不重置或重建丢失额度。生成器/裁判本轮为 0，旧 13 账本保持。

日常 `.env` 四开关保持关闭，真实验证由自有验收进程使用同一持久 T17D 总账本。`interactive_checks` 是独立开发验收工具：离线网关会明确显示 offline_control；真实网关的提交后工具回执丢失是明确控制，不能称为真实供应商宕机。开发场景及原失败独立保存，不并入 T13 保留集或 T16 成绩。

接口/权限/幂等/SQL 控制使用真实 MySQL 与 Redis；页面在深浅主题和窄屏检查。最终结果、限制与团队操作步骤另见 T17D 验收记录。仅完成本项后等待团队检查，不自动进入 T17E。
