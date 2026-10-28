# T07 故障配置与作用边界

2026-10-27。复用ac_fault_profiles/ac_test_runs/ac_test_executions/ac_trace_events，仍16表/23触发器，迁移0003不变。一次run只选择一种故障修订；未选配置为控制组。交互demo会话不注入故障。

## 创建和冻结

操作员Basic认证后POST `/api/v1/fault-profiles`；同profile_key/revision与相同内容幂等返回，内容不同409。GET列表使用cursor内部ID和limit1—100，GET `/{id}`读取；没有修改或删除接口。定义是操作员可共享的实验配置，原表没有owner列；执行结果仍按run所有者隔离。

```json
{
  "profile_key": "F04-DEMO", "revision": 1,
  "fault_type": "F04", "trigger_stage": "POST_COMMIT",
  "target_tool": "confirm_booking",
  "parameters": {"trigger_on_nth": 1, "trigger_window": 1, "delay_ms": 0},
  "seed": 27, "max_triggers": 1, "enabled_for_test": true
}
```

创建run增加可选fault_profile_id，服务端验证实际数据库内容/enabled，冻结ID、配置及config_hash；后续恢复读取冻结配置。同一run request_id重试仍受内容哈希约束；不传新字段时维持历史控制组请求哈希。不能从模型参数覆盖配置、身份、scope或事务钩子。

参数拒绝未知字段/脚本/payload。trigger_on_nth为1—50，trigger_window为1—10，max_triggers为1—5，seed为0—2000000000；F01 delay_ms仅0—500。工具/阶段必须与故障匹配。

## 故障效果

| 类型 | 工具与阶段 | 实际工具及SQL | 模型收到的结果 |
|---|---|---|---|
| F01 | 三个只读工具或confirm_booking，PRE_DISPATCH | 不派发该次业务调用；raw_result为空，dispatched=false | 显式timeout/504，not_committed、retryable=false；confirm授权撤销，后续提交需要新确认 |
| F02 | confirm_booking，PRE_COMMIT | 真实事务中执行并记录未提交rollback_probe，然后抛BookingError并回滚；before/after不变 | 可识别503/明确失败，not_committed；后续需新确认 |
| F03 | retrieve_rules/query_availability/lookup_booking_by_request，POST_RESULT | 正常只读查询，保留完整raw_result；撤回该次正确结果对可信状态/检索依据的更新 | 真实损坏JSON字符串，ToolMessage不将它重新编码为合法JSON；Agent可以恢复、澄清或安全停止 |
| F04 | 成功confirm_booking，POST_COMMIT | 真正提交后保留raw_result与SQL预约；非成功结果记not_applied | response_lost、commit_state=unknown及原request_key；不透传成功booking_id，保留未知提交状态，需原键查证 |

F02不是伪造一个失败返回：rollback_probe记录同一事务内部一度可见的预约，after记录回滚后的事实。F04不是回滚：实际提交不可被检查点/可见结果覆盖。确认绑定/租约校验先于注入，故障不能赋予提交权限。

## 重复、恢复与证据

execution.timing_json.faults持久保存profile_hash、eligible_calls/reserved/injected/not_applied及操作回执。操作键由源AI消息ID、实际tool_call_id和规范化工具参数决定。新Worker/新attempt沿用原计数；完成的同操作返回原回执，不重复派发；reserved但无可证明完成回执时停止并报fault_recovery_uncertain，不盲重放。

触发位置为trigger_on_nth加哈希取模trigger_window的偏移；哈希输入包含冻结profile_hash、配置seed及execution.seed。到位后在max_triggers限制内触发。相同配置和相同合格调用序列可重复定位；不宣称控制了模型随机性，也不保证Agent一定调用目标工具。

每个合格目标调用在原工具请求parent及tool_call_id下记录fault.scheduled；实际生效记录fault.injected，实际业务失败而未达到预期效果则fault.not_applied。事件包含ordinal、stage、seed、decision_hash、raw/visible、before/after及适用rollback_probe。大payload沿用脱敏后artifact；原始结果不裁掉后再冒充工具原文。跨attempt复用回执以普通引用保留旧事件ID，不创建违反数据库父事件约束的关联。

页面按冻结配置/计数展示，提供“只看故障”和实际观察/模型可见双栏。gateway_mode=offline_test_double时显示“离线模型复验”；实际工具、事务和MySQL/Redis仍是真实执行。只读打开证据不会重新派发工具或模型。

T07只验证注入效果及事实来源。任务完成、Agent可靠性和缺陷判定留给T08独立断言；inconclusive/cancelled均不等于Agent通过。团队复验、真实模型记录和限制见[T07验收](../acceptance/T07.md)。
