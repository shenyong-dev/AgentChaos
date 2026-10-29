# T14 工作区契约

GET /api/v1/workbench/overview、versions、cases、cases/{id}、runs均为认证operator只读发现接口。运行和对象严格按owner过滤；case仅当前批准dev修订，T13另按当前冻结选择，排除旧历史修订和校准/holdout。分页游标/limit和过滤参数服务端验证；没有GET自动登记版本、派发Worker或调用模型。

版本兼容性通过当前源码/SDK/默认知识manifest哈希比较；不等于Worker存活、付费允许或新模型能力。知识profile随用例返回；默认创建入口禁选非base配置，其余配置需要受信任运行环境，不能默默换知识。

创建、取消、输入复用既有runs协议，实际owner/revision/幂等约束仍由服务端执行。创建返回包未知时UI在标签页内存保留完整payload和UUID；导航/关闭弹窗不重新签发请求，退出/刷新清除内存。HTTP4xx明确拒绝与网络/5xx未知回执分别处理；不会把丢包当作没创建而再次提交新UUID。

三栏用同一execution身份联动；断言可按引用读取当前500窗口之外的事件，不推进同步游标。业务快照明确为执行时SQL记录，和实时查询、模型可见返回分开。新事件仅提示，用户明确跳转才滚动。SSE真实状态、连续游标和重复身份检查沿用既有协议。

只做已有Vue/Element Plus整合，无新状态持久化库/图形引擎/外部观测。缺陷管理/导出报告等保持T15范围；完整操作、真实与离线边界见acceptance/T14.md。
