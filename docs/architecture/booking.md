# T03 确定性预约接口与工具契约 v1.0

日期：2026-10-25，Asia/Shanghai。实现无模型依赖的预约服务，使用既有16表，不新增表或迁移版本。Agent、会话中断恢复和模型工具适配在T04实现。

## 直接接口

API入口：`http://127.0.0.1:8000/docs`。通过Authorize填写 `demo_operator` 与本地 `.env` 的 `DEMO_SEED_PASSWORD`；接口使用HTTP Basic，每次校验账号启用状态和operator角色。reviewer返回403；未认证/错误凭据返回401。`POST /auth/login`仍未实现。

| 方法/路径，均在 `/api/v1` 下 | 行为 | 成功HTTP |
|---|---|---|
| POST `/booking/availability` | 按容量、时段、场地和占用查询；最多50个候选，截断明确标记 | 200 |
| POST `/booking/drafts` | 撤销该身份旧待确认草稿，校验新参数并返回草稿、哈希、有效期及建议幂等键 | 201 |
| POST `/booking/confirm` | 校验明确确认与草稿，事务写预约及全部占用；同请求返回原预约 | 200 |
| GET `/booking/requests/{idempotency_key}` | 只查当前身份/scope的原请求，返回事实或not_found/unknown | 200 |
| DELETE `/booking/bookings/{booking_id}` | 取消当前身份预约并在同事务释放占用，保留预约及草稿历史；重复取消安全 | 200 |

HTTP默认绑定 `scope_id=demo`、`actor_key=user:<数据库用户id>`、服务器当前参考时钟。请求JSON不能提供actor/scope/reference_clock；未知字段拒绝。内部服务 `BookingContext` 用于可信执行器注入独立test范围和固定实验参考时钟，不是用户可任意填写的接口参数。未公开fixture清理HTTP接口。

成功响应为 `Envelope.data → BookingToolResult.data → 业务对象`。外层request_id只是本次HTTP追踪标识；业务idempotency_key必须跨重试保存，不能用新的X-Request-ID替换它。

工具结果另含status/code/summary/request_key/retryable/commit_state/evidence_refs。证据引用如 `draft:<UUID>`、`booking:<UUID>` 对应真实数据库行，不代表已有T06轨迹或报告文件。commit_state说明预约事实：查询为not_applicable，预览为not_committed，已找到预约为committed，无法确认则unknown；cancelled状态保留原预约曾提交的事实。

## 正常与异常请求示例

查询请求（省略room_code查询所有合适场地；日期须处于接口当前参考时钟后的7天内）：

```json
{
  "start_at": "2026-10-26T14:00:00+08:00",
  "end_at": "2026-10-26T15:00:00+08:00",
  "attendees": 10
}
```

预览请求在同一JSON中加 `"room_code":"A101"`，POST到drafts。将预览结果 `data.data` 的draft_id、draft_hash、suggested_idempotency_key保存；确认请求为：

```text
{
  "draft_id": <预览返回的draft_id字符串>,
  "draft_hash": <预览返回的draft_hash字符串>,
  "idempotency_key": <原suggested_idempotency_key字符串>,
  "confirmed": true
}
```

以上确认部分是替换占位的格式说明，不是固定可提交数据。完整真实请求闭环结果见 [http-roundtrip.json](../evidence/T03/http-roundtrip.json)，其对象属于已删除的临时库，不能拿这些UUID在主库操作。团队直接验收时通过当前接口生成自己的草稿，先核对完整场地、时间和人数，再确认；确认会在demo中创建预约，取消会保留历史行。

| 异常操作 | HTTP / code | 数据行为 |
|---|---|---|
| 缺日期/时间/人数，confirmed缺失或类型不符，注入scope/actor | 400 / validation_error | 不接受不完整或越界输入；澄清对话留T04 |
| confirmed=false | 400 / confirmation_required | 不提交 |
| 时间未对齐、时长不在30—120分钟、超7天或早于参考时钟 | 400 / time_alignment、invalid_duration、outside_booking_window | 不创建新预约 |
| 超容量/开放时间不符 | 400 / capacity_exceeded、outside_opening_hours | 不创建新预约 |
| 预览后已被其他身份预约 | 409 / slot_conflict或booking_conflict | 整个提交回滚，无半笔占用 |
| 同幂等键用于另一个草稿或哈希 | 409 / idempotency_conflict | 原预约不变，不创建第二笔 |
| 日期/人数/场地变化后使用旧草稿，或换键复用已确认草稿 | 409 / draft_not_pending | 拒绝旧确认 |
| 确认哈希不符或持久草稿参数被改写 | 409 / draft_mismatch | 不提交 |
| 墙钟到达草稿有效期 | 410 / draft_expired | 不提交 |
| 访问其他身份/范围的草稿或预约 | 404 / draft_not_found、booking_not_found | 不暴露别人的对象 |
| 规则配置损坏 | 503 / room_rules_invalid | 平台配置错误，不冒称没有可用场地 |
| 写入通信失败 | 503 / booking_state_unknown | 结果可能已提交，先用原键查证 |

已通过结构校验的预览请求会撤销旧pending草稿，包括新参数因容量/时段等业务条件被拒绝的情形。撤销在事务提交后才返回该业务错误；此时没有新预约，但旧草稿确实已失效。传输结构不合法、未授权或数据库通信未完成时不能承诺撤销已完成。T04还须在用户改条件/拒绝确认时撤销会话确认授权，不能从工具失败回退为旧用户授权。

草稿TTL为服务端墙钟5分钟，到期时立即拒绝；时间窗口检查用BookingContext.reference_clock，可固定实验时间，与真实created_at和墙钟TTL分别记录。场地开放时间采用Asia/Shanghai规则，入库存UTC。开始/结束均30分钟对齐、区间左闭右开，单次30—120分钟，开始不早于参考时钟、结束不晚于参考时钟加7天。

## 幂等、并发和查证

同scope/actor的写操作通过短时MySQL命名锁串行化，确认及取消再锁场地行；事务同时更新草稿状态、预约和所有占用片。占用片唯一键兜底冲突，不依赖“先查到空闲”保证提交成功。取消释放占用片但不删除历史预约，重复确认已取消的同一请求仍返回原cancelled记录，不能重新激活。

当前命名锁等待上限3秒，InnoDB行锁等待5秒，DB socket读/写各15秒；这些是分项上限，不是完整HTTP或Agent执行总时限。连接归还池前显式释放命名锁，释放通信失败则使连接失效。没有自动重试写入，也不把一次会话的模型等待放在数据库事务里。

同请求提交在途时，另一次确认可能返回503 booking_busy/unknown；查证未见记录也返回 `status=not_found, commit_state=unknown`，因为未提交事务随后可能成功。查询失败同样不能证明预约失败。保留原键，先查证；需要恢复时仅用同一草稿/哈希/键。不要从not_found或超时生成新请求键。成功提交即使草稿后来过期或参考时钟前移，完全相同的重试仍返回原记录。

命名锁面向当前单MySQL开发拓扑；多主、连接代理语义、Worker租约和过期写入隔离尚未验证。T05需在修改事务中接入执行租约检查，不能把T03互斥当作Worker恢复或生产多租户能力已完成。

## 给T04的四项工具契约

[booking-tools.schema.json](booking-tools.schema.json)从 `app/tools/booking.py` 导出，定义四个名称/版本、读写类型、输入输出、权限、分项超时和零自动重试策略。取消暂不作为模型工具。

- query_availability：只读；结果为当时快照。
- preview_booking：写草稿并撤销旧草稿；不能自动反复调用来假装无副作用查询。
- confirm_booking：相同键和完整确认绑定具备幂等性。**模型输入Schema不含confirmed**；直接HTTP接口的明确确认字段与模型工具输入分开，T04必须从合法用户确认/恢复流程注入授权，模型不得自授确认。
- lookup_booking_by_request：只读；只查当前可信身份和scope，不把缺失结果当作确定失败。

T04后续已实现ToolRegistry适配、总时限、最大返回16384字节限制、确认门及Agent会话状态，见[Agent基线](agent.md)。模型身份/授权由服务端注入，草稿按会话隔离；上述四工具v1.0契约继续沿用。

## 已执行验证与依据

[T03验收记录](../acceptance/T03.md)保存真实MySQL并发/回滚/回执丢失验证与HTTP证据；[只读网络检查脚本](../../backend/scripts/verify_booking_api.py)可重跑主库查询与认证检查，不创建草稿或预约。集成写入仅在随机临时库中执行并最终删除。

实现语义参考：[MySQL锁定读](https://dev.mysql.com/doc/refman/8.0/en/innodb-locking-reads.html)、[SQLAlchemy事务上下文](https://docs.sqlalchemy.org/en/21/orm/session_transaction.html)。这些说明不替代本项目真实并发测试。
