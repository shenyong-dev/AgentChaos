# T01 接口与数据契约 v1.0

日期：2026-10-24。本文区分已实现接口与后续约定；不代表预约、评测或故障引擎已完成。

## 已实现接口

| 方法与路径 | 实际行为 | HTTP |
|---|---|---|
| GET `/api/v1/health/live` | 只确认 API 进程可响应，不连接模型或数据库 | 200 |
| GET `/api/v1/health` | 并行执行 MySQL 只读查询、Redis PING 和 JSON/Search 能力检查；独立报告模型配置状态 | 基础设施可用 200，否则 503 |
| GET `/api/v1/system/config` | 返回角色、模型名、输出上限、预算、调用开关等公开配置；只报告密钥是否存在 | 200 |
| GET `/docs`、`/openapi.json` | FastAPI 实际文档与 schema | 200 |

`infrastructure_ready` 仅表示 API、MySQL、Redis 必需能力可用。模型的 `not_configured`/`not_probed` 单独显示，不能据基础设施状态宣称模型已连通。健康检查不会调用模型、创建业务表或执行预约。

成功响应统一为 `{"data": ..., "request_id": "UUID"}`。`X-Request-ID` 使用传入的有效 UUID，否则由服务端新建；响应头与响应体保持一致。默认 `Cache-Control: no-store`。

错误响应统一为 `{"error":{"code":"...","message":"...","details":[]},"request_id":"UUID"}`。不回显异常原文、密钥或被拒绝输入。参数验证采用 400；未来身份/权限使用 401/403，冲突使用 409，预算或限流使用 429。T01 尚无身份、创建运行或修改配置接口；这些路径目前返回真实 404。

健康 503 是可阅读的健康报告，仍使用 data 结构，前端不得把它误当成成功全绿，也不得仅因 HTTP 503 丢弃依赖诊断。

## 当前可校验的数据结构

- [OpenAPI](openapi.json)：从当前 FastAPI 应用生成。
- [用例 JSON Schema](test-case.schema.json)：从 `TestCaseDraft` 生成。
- [事件 JSON Schema](trace-event.schema.json)：从 `TraceEvent` 生成。

生成命令：在 `backend` 执行 `uv run --locked python scripts/export_contracts.py`。生成过程不调用模型、不连接数据库。修改契约时同步 schema、示例、消费者与版本记录。

### 用例 DSL

`schema_version=1.0`；`case_key + revision` 定位不可变用例修订，`family_key` 标识不可跨开发/保留集的家族。`dataset_split` 为 dev/calibration/holdout，`reference_clock` 必须带时区，`fixture` 是版本化初始数据标识。`turns` 为有序用户输入，不允许从输入指定服务端身份。

类别为 normal、tool_execution、memory_and_tool、retrieval、security、robustness，分别对应规划的六类覆盖。当前 schema 只定义规划示例中的 booking_matches、booking_count、no_booking_on 三种断言；它们尚未执行。T08 扩展时先更新契约，不把未实现断言静默当通过。

当前限制：1—50 轮，每轮最多 12000 字符，1—100 条断言。未知字段和未知断言类型拒绝解析，避免拼写错误被忽略。限制是首版传输边界，不是正式数据集规模。

review_status 为 draft/approved/rejected。正式批准必须来自后续服务端审核流程；导入文件自带的 approved 不能替代审核。T01 的 Pydantic 校验只检查结构，不证明语义或预期正确。

### 事件

外部 event_id/run_id/execution_id 使用 UUID；attempt、seq 从 1 开始，重复进入节点以 node_visit 区分。事件类型为 run/execution/node 开始结束、retrieval.completed、tool.requested/completed、fault.injected、state.changed、assertion.completed、budget.exceeded、execution.cancelled/error。

`timestamp` 必须带时区；`payload` 保存原始可观察证据，`evidence_refs` 指向关联证据。字段 node、node_visit、tool_call_id、parent_event_id 在适用时填写。持久表的 parent_event_uuid 对应协议 parent_event_id。身份和 scope 由服务端绑定，不相信模型声明。

T06 才实现事件落库和 SSE。届时先持久化再推送，以持久事件游标续传，前端按 event_id 去重；事件中执行 attempt 的序列不能互相覆盖。

## 后续接口约定

对象/版本、用例/审核、故障配置、运行/取消、执行/事件、缺陷/复核、版本对比/报告、预约演示沿用任务规划第 7 章。T01 不提供返回固定成功值的占位实现。

后续列表采用 `data.items`、`data.next_cursor`，默认 limit=20、最大 100；以稳定 ID/创建顺序分页。创建运行必须保留幂等创建标识与冻结清单，基础设施恢复 attempt 不增加计划重复次数。

MySQL 常规内部 ID 为 BIGINT UNSIGNED；时间列为 DATETIME(6) 并按 UTC 写入，API 传带偏移 ISO 8601，界面显示 Asia/Shanghai。参考实验时钟与运行墙钟分别保存。对象、知识、工具、用例及评价器版本进入运行清单；16 表迁移属于 T02。
