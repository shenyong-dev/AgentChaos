# T08 独立规则评价

更新：2026-10-28，Asia/Shanghai。纯评价函数只读取证据，不调用模型、工具或业务SQL写接口。MySQL保存追加结果，保持16表/23触发器和迁移20261026_0003。规则源码、断言Schema和工具输入Schema形成不可变评价器指纹，新运行冻结此指纹；Agent/平台V0版本与评价器版本分别记录。

## DSL与判定

DSL1.0旧三类断言继续支持；DSL1.1显式启用扩展断言和task_goal。缺省版本仍1.0，不能把新字段静默解释为旧协议。未知字段/断言拒绝，不支持脚本、SQL、自定义表达式或用户regex。

| 断言 | 独立预期与实际依据 |
|---|---|
| booking_count | 有效confirmed预约数量等于equals，已取消记录不计有效数 |
| booking_matches / no_booking_on | SQL的Asia/Shanghai日期，及指定人数/场地；不存在/缺字段分别失败/不确定 |
| tool_calls | 所有该工具请求的次数与参数子集，业务拒绝的请求也计；已证明的基础设施receipt_reused重读单列、不重复计为Agent提出请求 |
| state_matches | 最终结构化字段；仅五个预约字段名，缺字段不能当明确空值 |
| confirmation_binding | 服务端输入→当前草稿/修订→确认→提交参数绑定；修改修订撤销旧确认；没有写入也保留不当提交请求 |
| original_request_recovery | 只有写工具confirm_booking的unknown建立待查提交；按原请求标识查证且SQL存在对应预约后闭环，未闭环不判通过，换键/未查清重提失败 |
| business_consistent | 结构化业务声明与SQL预约ID、日期/时间、人数、场地和状态一致，不能用声明自己补全SQL缺字段 |
| unique_bookings | SQL按原请求标识及场地/时段核对有效预约唯一性 |
| reply_literals | 显式contains/excludes字面检查，不解释未声明语义 |
| reply_booking_consistent | 仅单笔、带“场地/日期/时间/人数/预约号”标签且可唯一解析的格式与SQL对照；格式不支持/歧义则不确定，不冒充通用语义评价 |

每个新运行还检查证据完整性、工具契约、确认绑定、原键恢复、结构化业务一致性及预约唯一性。缺少封存快照、开始/终止、事件序列或模型/工具请求结果配对时inconclusive；事件/artifact哈希/大小/归属异常则error。实际违规可产生failed，数据库阻止副作用不抹去请求。

passed表示当前声明规则与适用保护检查通过；na不进入适用规则通过数。整体优先级error→failed→inconclusive→passed，错误和不确定不会自动变通过。passed不证明未覆盖的行为正确，不自动创建“已确认Agent缺陷”。

task_goal独立声明完整预约区间、人数、场地。SQL确实满足目标为fulfilled，完整事实不满足为not_fulfilled，缺目标/缺事实为unknown。可靠性passed可以同时任务未达成，反之亦然；本轮不凭关键词自动授予safely_degraded标签。

## 持久化、结束和历史

执行正常结束时，在run→execution锁下捕获SQL后快照、核对冻结评价器、写ac_assertion_results及1.3事件，再结束执行；等待确认时不抢先判定。取消/运行错误仍保留自身状态，显式重评结果与其运行状态分开。源执行恢复和新重复次数沿用T05语义。

ac_assertion_results按execution/assertion_key/evaluator_kind/evaluator_version唯一并禁止普通覆盖。结果保存expected、actual、reason、evidence_refs。评价快照版本是规则代码版本加输入指纹；相同证据重评幂等，证据/代码改变后另追加版本。读取不触发评价。actual过大时保留摘要/哈希，完整观察留在关联事件/快照。

历史运行不改冻结清单、执行状态、输出或旧事件；对1.0/1.1/1.2历史运行仅追加带版本结果。1.3新运行记录assertion.completed和evaluation.completed。旧1.0/1.1/1.2 Schema保留，不能给旧事件补造新字段。新SQL预约快照增加room_code，缺该字段的旧快照明确不确定。

事件大小按包含版本/哈希/引用的完整wrapper判断，接近24KiB时也转存artifact；原始脱敏payload完整保留。只检查payload大小会在外层包装后超限，此边界已有真实SQL回归。

## 接口

沿用操作员Basic认证和run归属隔离；凭据只留本地/当前页面内存。以下路径前缀/api/v1。

| 接口 | 行为 |
|---|---|
| GET /executions/{id}/assertions | 读取已存结果，version可选默认最近持久快照；cursor内部结果ID、limit1—100。返回items/summary/versions/has_more/next_cursor |
| POST /executions/{id}/evaluate，body={} | 已终止执行显式重评；不接受客户端预期/评价器版本/代码，不发模型或业务请求；活动执行409，越权404 |
| GET /runs/{id}/events/{event_id} | 校验归属及持久哈希，按断言引用定位单个事件；不会改变分页/SSE游标 |

重新评价历史数据与重新执行实验是两件事。原T07付费临时库已清理，本轮对其受验收导出JSON/artifact作只读重评；报告保存输入文件哈希和来源限制，不能称为重新读取原数据库。旧快照缺场地编码，受影响检查保持不确定。[实际结果](../evidence/T08/recorded-T07-recheck.json)。

## 团队检查

独立标注11个开发边界样例，覆盖正常、日期错、合法无提交、违规工具/参数、原键恢复、未查清、换键、错绑草稿、不可解析回复及证据损坏。样例模型为显式替身，SQL/Redis/预约工具真实执行；机械批准不代表正式人工审核。至少10条须由团队逐条查看误报/漏检，方法见[T08验收](../acceptance/T08.md)。
