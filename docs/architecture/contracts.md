# T01—T09 接口与数据契约

更新日期：2026-10-28。已实现工程接口、确定性预约、交互Agent、持久执行器、事件/快照/SSE、四类故障及独立规则评价。DSL1.1兼容1.0，事件1.3兼容旧1.0/1.1/1.2读取；正式审核/AI评价/缺陷报告仍在后续任务。

## 已实现接口

| 方法与路径 | 实际行为 | HTTP |
|---|---|---|
| GET `/api/v1/health/live` | 只确认 API 进程可响应，不连接模型或数据库 | 200 |
| GET `/api/v1/health` | 并行执行 MySQL 只读查询、Redis PING 和 JSON/Search 能力检查；独立报告模型配置状态 | 基础设施可用 200，否则 503 |
| GET `/api/v1/system/config` | 返回角色、模型名、输出上限、预算、调用开关等公开配置；只报告密钥是否存在 | 200 |
| POST `/api/v1/booking/availability`、`/booking/drafts` | HTTP Basic认证后查询或生成草稿，可信身份/scope由后端绑定 | 200 / 201 |
| POST `/api/v1/booking/confirm` | 明确确认、草稿绑定、事务占用与同键幂等 | 200 |
| GET `/api/v1/booking/requests/{idempotency_key}` | 按原键查证当前身份/scope的预约事实 | 200 |
| DELETE `/api/v1/booking/bookings/{booking_id}` | 事务取消并释放占用，历史保留，重复取消安全 | 200 |
| POST/GET `/api/v1/demo/sessions`及`/{session_id}` | 认证创建/读取绑定不可变版本的Agent会话，创建只接受空对象 | 201 / 200 |
| POST `/api/v1/demo/sessions/{session_id}/messages`、`/resume`、`/retry` | 真实模型对话、合法中断恢复和显式失败节点重试；请求UUID/修订保护 | 200，冲突409，预算429，模型服务503/超时504 |
| GET `/api/v1/demo/sessions/{session_id}/history` | 只读分页原始用户/助手消息，不重放模型或工具 | 200 |
| GET `/docs`、`/openapi.json` | FastAPI 实际文档与 schema | 200 |
| POST/GET `/api/v1/runs`、`/{run_id}`及取消/补投/输入/恢复子路径 | 认证的冻结运行、持久状态和Linux执行控制，详见[执行器契约](executor.md) | 创建201、读取/控制200；参数400、越权404、冲突409 |
| GET `/api/v1/runs/{id}/snapshot`、`/events`、`/events/stream`、`/artifacts/{id}`及`/executions/{id}`、`/events` | 已提交快照/事件、按持久游标续传及受控证据下载，详见[事件契约](evidence.md) | 200；认证401/403、归属404、游标400/409/410、证据完整性503 |
| POST/GET `/api/v1/fault-profiles`及GET `/{id}` | 认证创建不可变故障修订、分页/读取；run可选fault_profile_id并冻结哈希，详见[故障契约](faults.md) | 创建201、读取200；参数400、认证401/403、不存在404、修订冲突409 |
| GET `/api/v1/executions/{id}/assertions`、POST `/{id}/evaluate` | 读取已存判定，或已终止执行显式追加评价快照，详见[规则评价](evaluation.md)；GET不执行评价，POST不调用模型/工具 | 200；参数400、认证401/403、归属404、活动执行409 |
| GET `/api/v1/runs/{id}/events/{event_id}` | 按断言引用定位同运行持久事件，不改变游标或重放工具 | 200；归属/不存在404、完整性503 |

`infrastructure_ready` 仅表示 API、MySQL、Redis 必需能力可用。模型的 `not_configured`/`not_probed` 单独显示，不能据基础设施状态宣称模型已连通。健康检查不会调用模型、创建业务表或执行预约。

成功响应统一为 `{"data": ..., "request_id": "UUID"}`。`X-Request-ID` 使用传入的有效 UUID，否则由服务端新建；响应头与响应体保持一致。默认 `Cache-Control: no-store`。

错误响应统一为 `{"error":{"code":"...","message":"...","details":[]},"request_id":"UUID"}`。不回显异常原文、密钥或被拒绝输入。参数验证采用400；身份/角色使用401/403、冲突409、草稿过期410、依赖故障503、预算或限流429。预约错误的details含retryable/commit_state及适用的原请求键。独立登录或修改配置接口仍返回404。

健康 503 是可阅读的健康报告，仍使用 data 结构，前端不得把它误当成成功全绿，也不得仅因 HTTP 503 丢弃依赖诊断。

## 当前可校验的数据结构

- [OpenAPI](openapi.json)：从当前 FastAPI 应用生成。
- [用例 JSON Schema](test-case.schema.json)：从 `TestCaseDraft` 生成。
- [事件v1.3 JSON Schema](trace-event.schema.json)：严格payload联合；[保留v1.0](trace-event-v1.0.schema.json)、[保留v1.1](trace-event-v1.1.schema.json)、[保留v1.2](trace-event-v1.2.schema.json)供历史解析。
- [规则断言Schema](assertion.schema.json)：当前声明式类型；[原用例DSL1.0 Schema](test-case-v1.0.schema.json)保留。
- [故障配置 JSON Schema](fault-profile.schema.json)：F01—F04的阶段、工具、种子和触发限制。
- [预约工具契约](booking-tools.schema.json)：从T03输入输出及静态策略导出；直接HTTP接口以OpenAPI为准，模型工具不接收confirmed/身份/scope/时钟。
- [Agent六工具](agent-tools.schema.json)：T04实际ToolRegistry输入，另含有引文的字段提取和规则检索；执行与来源边界见[Agent说明](agent.md)。

生成命令：在 `backend` 执行 `uv run --locked python scripts/export_contracts.py`。生成过程不调用模型、不连接数据库。修改契约时同步 schema、示例、消费者与版本记录。

### 用例 DSL

`schema_version`支持1.0/1.1，缺省1.0保持兼容；扩展断言和task_goal必须显式1.1。`case_key + revision` 定位不可变用例修订，`family_key` 标识不可跨开发/保留集的家族。`dataset_split` 为 dev/calibration/holdout，`reference_clock` 必须带时区，`fixture` 是版本化初始数据标识。`turns` 为有序用户输入，不允许从输入指定服务端身份。

类别为 normal、tool_execution、memory_and_tool、retrieval、security、robustness，分别对应规划的六类覆盖。T08执行原三类SQL断言，并增加工具/参数、状态、确认、查证、唯一性、业务一致性及限定回复检查。缺证据不确定、未知断言拒绝/错误，详见[评价契约](evaluation.md)。可选task_goal独立核对SQL目标，不把任务未达成等同为Agent违规。

当前限制：1—50 轮，每轮最多 12000 字符，1—100 条断言。未知字段和未知断言类型拒绝解析，避免拼写错误被忽略。限制是首版传输边界，不是正式数据集规模。

review_status 为 draft/approved/rejected。正式批准必须来自后续服务端审核流程；导入文件自带的 approved 不能替代审核。T01 的 Pydantic 校验只检查结构，不证明语义或预期正确。

### 事件

外部event_id/run_id/execution_id使用UUID；执行级attempt/seq从1开始，重复节点以node_visit区分，run级execution_id/attempt为空。已实现run/execution/node生命周期、模型/工具/检索、状态/确认/上下文/检查点、预算/取消、故障及assertion.completed/evaluation.completed事件。旧运行的显式重评不修改旧事件协议。

`timestamp` 必须带时区；`payload` 保存原始可观察证据，`evidence_refs` 指向关联证据。字段 node、node_visit、tool_call_id、parent_event_id 在适用时填写。持久表的 parent_event_uuid 对应协议 parent_event_id。身份和 scope 由服务端绑定，不相信模型声明。

T06已实现事件落库与原生SSE。v1.1补充真实scope/run_seq、可空execution/attempt和类型化payload；T07的v1.2增加故障阶段/计数/原始与可见结果、dispatch和回执复用。旧运行按原版本读取，不给历史payload补造新字段。先持久化再读取推送，按run提交序列游标续传，前端按event_id去重；详见[事件契约](evidence.md)。

T08的v1.3增加评价事件；新运行冻结规则代码/Schema指纹，旧1.2 Schema保存。预约快照新增SQL读取的room_code，旧快照缺该字段不会自动补全。结果API提供预期、实际、原因、版本和事件/快照引用；认证和错误封装沿用既有协议。

T09事件仍1.3/DSL1.1，不新增HTTP端点。目标manifest增加context策略，Worker按冻结知识修订构造索引，普通拒绝保存来源；健康配置task为T09。客户端仍不能指定知识修订、actor/scope/grant，详见[多轮与检索边界](dialogue-boundaries.md)。

## 后续接口约定

对象管理、用例/审核、缺陷/复核、版本对比/报告沿用任务规划第7章，尚未实现。T03预约接口、T04会话、T05运行/取消、T06执行事件、T07故障配置及T08断言/重评已实现，详见[预约接口](booking.md)、[Agent基线](agent.md)、[执行器](executor.md)、[证据契约](evidence.md)、[故障契约](faults.md)与[评价契约](evaluation.md)。

后续列表采用 `data.items`、`data.next_cursor`，默认 limit=20、最大 100；以稳定 ID/创建顺序分页。创建运行必须保留幂等创建标识与冻结清单，基础设施恢复 attempt 不增加计划重复次数。

MySQL常规内部ID为BIGINT UNSIGNED；时间列为DATETIME(6)并按UTC写入，API传带偏移ISO 8601，界面显示Asia/Shanghai。参考实验时钟与运行墙钟分别保存。对象、知识、工具、用例及评价器版本进入运行清单。

## T02持久化补充，2026-10-25

16表迁移、版本保护、复合scope/身份关联、种子及fixture工具已实现，见 [字段字典及ER图](database.md) 和 [操作说明](../database-operations.md)。此处持久化结构不增加HTTP接口；预约、审核、运行和SSE仍按后续任务实现，事件JSON Schema仍为v1.0。健康检查MySQL成功文本调整为“已连接应用数据库”，不再声称库内无业务表。

T03补充：确定性预约服务现已落地，接口与限制见[booking.md](booking.md)；没有增加表、修改迁移版本或升级事件DSL。T02段落中的“预约后续实现”是当时交付边界。
