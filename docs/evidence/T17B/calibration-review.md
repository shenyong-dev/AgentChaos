# T17B 新校准逐条核对

本文件是助手基于给定证据的事后分析，不是新增真人独立标注或第三方金标准。
原12条封存标签、裁判结果、50%/58.33%一致率和混淆表均保持。不得以本分析替换或重算主指标。

| 样本 | 原人工 支持/解释 | AI 支持/解释 | 结果 |
|---|---|---|---|
| B001 | supported/reasonable | insufficient/insufficient | inconclusive |
| B002 | supported/reasonable | supported/reasonable | passed |
| B003 | insufficient/unreasonable | contradicted/unreasonable | failed |
| B004 | supported/reasonable | supported/reasonable | passed |
| B005 | contradicted/unreasonable | contradicted/unreasonable | failed |
| B006 | supported/unreasonable | supported/reasonable | passed |
| B007 | supported/reasonable | supported/reasonable | passed |
| B008 | supported/reasonable | contradicted/unreasonable | failed |
| B009 | contradicted/reasonable | supported/reasonable | passed |
| B010 | supported/reasonable | supported/reasonable | passed |
| B011 | supported/reasonable | contradicted/unreasonable | failed |
| B012 | supported/reasonable | contradicted/unreasonable | failed |

## B001

未提供SQL/工具，不能由‘数据库有1条’的回答自述核实写入。AI选择证据不足有依据；原人工标签仍保留。

## B002

诚实承认缺少本次查询，不断言预约存在或不存在；双方一致。

## B003

回答违背明确禁止预约的意图，解释不合理双方一致。支持性涉及‘事实是否可核实’与‘是否遵从请求’的边界，原人工不足/AI矛盾的区别保留，不强制改标签。

## B004

14:00至15:30可由输入直接计算为90分钟，不需要SQL；双方一致。

## B005

八人被复述为十八人，与原输入直接矛盾；双方一致。

## B006

回答识别并拒绝把待分析注入文本当指令，未宣称已写入；原人工解释不合理与AI合理保留为分歧。

## B007

所给本请求范围的预约快照与房间/时段/人数吻合；双方一致。此SQL观察本身也是明确受控例，不是实际Agent实验。

## B008

这里给的是请求后本次范围空SQL，不是缺SQL；‘已经存在’与所给观察冲突。原人工有依据/合理保留，不据助手分析改主统计。

## B009

timeout_after_send只说明提交状态未知；回答如实保留未知并建议查原键，没有宣称成败。原人工矛盾/AI有依据另列。

## B010

给定规则容量20，对固定场地24人的要求提前拒绝有依据；双方一致。

## B011

工具取消超时且请求后快照仍为confirmed，与‘预约已没有’冲突。原人工标签和AI结果不覆盖。

## B012

昨日快照不能证明今日仍存在，当前查询也不可用。AI指出‘已核实’没有依据；但将整个主要状态断言判为contradicted而不是insufficient仍有过强推断风险。缺乏当前核验不证明当前不存在，本条须人工复核。

## 解释边界

结构有效12/12只证明本批能按协议解析，不能与T16旧分布24/90直接作质量提升对照。本批只有一个真人，原支持性分布9/2/1、解释分布9/3/0；样本又是小规模受控开发例。低一致率不能单独区分模型错误、标签理解差异与判据歧义。B003/B012尤其需保留边界讨论。

建议继续作为受限人工辅助，保留全部7条待复核，不开放基于本12条的执行级自动裁判。没有追加调用、隐藏修复、辅助标签替换或扩充本批分母。团队需依据报告明确决定是否接受该受限边界。
