# T13 数据集冻结契约

固定服务端datasets/v1.1/candidate.json，客户端只能提交hash绑定的真实reviewer确认。类别、60/30/30、家族、关键20、公开夹具算术、文本环境精确重复及内容哈希预检；这些不冒充Agent执行或完整语义判定。独立预期来自可阅读实验设定及实际用户审阅，不调用被测业务校验函数反推答案。

当前正式清单120条，SQL保留123条历史修订；117条复用，3条revision2。原初版120条不可变；资料按独立修订入库，初版两个和修订两个dataset_manifest owner artifact分别保存实际审核与源码/知识/故障绑定。16表/23触发器保持，0006只扩充owner证据CHECK；有dataset证据时回退拒绝。真实Reviewer身份与时间在服务端，导入JSON自带approved不构成批准。文件哈希和SQL保护有权限边界，不是防管理员审计系统。

GET /api/v1/datasets/current返回覆盖、规划、冻结回执和版本映射。GET /current/cases/{key}只允许reviewer读calibration/holdout，operator仅dev；其他operator拒绝。POST /current/review需reviewer、包hash、20关键ID及明确逐条/预期/规模声明；相同UUID原样重放读取既有决定，不重复批准或付费。不接受任意文件路径、Prompt、客户端来源或审批身份。

服务端冻结者将每种知识profile注册为V0配置。基础知识版本26，其他27—37；初版14—25保持且退出正式实验清单；不得按最大ID自动选择。compile_dataset_experiment.py只校验和输出统计，不会创建/调度任务或调用模型。V0草案含故障profile_id及可信document_ids；自定义KB由受信任RunService选择，通用默认KB创建入口不会冒充支持。计划repeat另列，run repetitions=1，基础设施attempt独立。V1未登记会拒绝，不假造对照。

全后端源码哈希会随后续接口改变，故保留不含密钥的当前独立源码快照frozen-source-v1.1.zip（初版快照另保留），实际D盘只读重建12配置一致。未来实验需准备隔离V0运行和证据共享/导出，不能覆盖旧冻结版本来绕过检查。上下文、检索词典/BM25、工具Schema、模型/SDK等完整组件在证据的frozen-target-versions.json。

120是不同用例，77是手工标签簇，不是统计独立样本数。作者助手看到holdout用于构建/审计，没有发送给被测Agent/生成器/judge或用来调优。规范化精确重复0不证明没有语义近似；人工已审具体清单，历史复核已发现3条与T09同族，经用户明确批准替换并追加revision2，旧版退出正式清单。运行时校验只允许这3条变化，其他117条须与初版一致；后续发现问题仍须新修订，不声称算法穷尽所有语义近似。T12既有校准不等于本批calibration；仅回答E1视图仍需独立校准。

具体case、预算边界、真实确认、检查命令、限制与证据见docs/acceptance/T13.md；不能由冻结成功推断Agent通过或自动开始正式实验。
