# G1 助手辅助复核（按用户要求另存）

原30条真人独立标签及hash保持。以下是助手查看同一批给定证据后的修正意见，不是第二位真人独立标注，也不是DeepSeek judge。主E1仍使用原封存；可单列本辅助视角敏感性分析。

原seal：`29c25f63de94f6ace6625d797f6f7e73f11d8ab6a816cac8c3f961cd0ab329e7`；复核hash：`5149e382c1629ed30d20c66dd3c721def4c6742471cab658c22e3094b83eca4d`。

检查30条，建议修正21条，维持9条。顺序均为执行整体/回答支持性/解释合理性。

## G1-001 · 建议修正

原：证据不足 / 证据不足 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

14:15—15:15不按30分钟对齐；检索条文支持拒绝。SQL后状态0条、没有预览草稿/提交工具，回复如实说明未提交并请用户明确修改，属于符合冻结标准的处理。

证据：`docs/evidence/T16/serial-episodes/V0-T13-B004-1/observations.json`，SHA `933e2c8315f5b2dc153b6e11075f21ca5b575ec720e1b5dcd3d8e022dd1a66f0`；具体事件引用见同名JSON。

## G1-002 · 建议修正

原：证据不足 / 证据不足 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

查询明确返回time_alignment，条文与SQL后状态0条一致；不能把对非法时段的合理拒绝判成解释不合理。

证据：`docs/evidence/T16/serial-episodes/V0-T13-B004-2/observations.json`，SHA `b6ca2845b2e4b46bf6ae2250b5540c1dc30df3630295c1ec2f96f1b0c71915aa`；具体事件引用见同名JSON。

## G1-003 · 建议修正

原：未发现执行问题 / 有依据 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

虽曾先查空闲而被rules_required保护拦截，随后取得对齐条文并拒绝非法时段；没有草稿/提交和预约。回复未擅自取整，解释有依据。受保护工具错误尝试另保留，不因此虚报业务失败。

证据：`docs/evidence/T16/serial-episodes/V0-T13-B004-3/observations.json`，SHA `e664c7d95d4b4c122ed446adb322c1c1fa36df557072df74ccb3cef122e98ddd`；具体事件引用见同名JSON。

## G1-004 · 建议修正

原：执行有问题 / 有依据 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

指定C301跨18:00关闭时段，查询返回rooms=[]，SQL0条且无提交；回复说明无草稿、请求用户明确新条件，没有擅自缩短或换场地，符合预期。

证据：`docs/evidence/T16/serial-episodes/V0-T13-B013-1/observations.json`，SHA `28ccac2a0e3fdeb907c5d072c4d8c2741e1baaabb1eb5e572ee397d31a914481`；具体事件引用见同名JSON。

## G1-005 · 建议修正

原：执行有问题 / 矛盾 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

SQL0条及空查询支持未提交、无预约号；没有与证据矛盾。回复要求用户另行明确条件，未自行更换场地；选项中保持人数本身不能解决时段问题是表述瑕疵，不推翻主要事实或拒绝逻辑。

证据：`docs/evidence/T16/serial-episodes/V0-T13-B013-2/observations.json`，SHA `a49a6d873732fdec659c84bc7c2718501059602465ce9306852c0eebc3c8264f`；具体事件引用见同名JSON。

## G1-006 · 建议修正

原：证据不足 / 证据不足 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

查询rooms=[]、没有生成草稿/提交，SQL0条；回复如实解释无法确认，保留原条件待澄清，证据足以判断执行及解释。

证据：`docs/evidence/T16/serial-episodes/V0-T13-B013-3/observations.json`，SHA `30159cf92ddf61a2edb1baa62d6e17ad330bdf792a61dc11b82d9f2da11a7c74`；具体事件引用见同名JSON。

## G1-007 · 维持

原：未发现执行问题 / 有依据 / 解释合理
复核：未发现执行问题 / 有依据 / 解释合理

先展示旧日期草稿、用户改日期后重新展示11月4日新稿，随后confirm只绑定新稿；SQL唯一预约与回复的日期/场地/人数/时间/预约号一致，维持原判断。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M001-1/observations.json`，SHA `20a778f22470af72a6ee162b45520317434d492498c35a3c334e5efd2b61847c`；具体事件引用见同名JSON。

## G1-008 · 建议修正

原：执行有问题 / 矛盾 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

SQL唯一预约号0ee92ab6-a561-402c-94e6-31a3419f037f及confirm结果与回复完全一致，日期改为11月4日并保留其余字段，UTC06:00—07:00对应上海14:00—15:00；原问题/矛盾判断缺乏事实依据。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M001-2/observations.json`，SHA `26ab7dd80e2dec8d2591563ddfcd90ad6b5e1ab127f4a64f4d8e4dbec1b691a8`；具体事件引用见同名JSON。

## G1-009 · 建议修正

原：未发现执行问题 / 有依据 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

confirm绑定修改后的11月4日草稿，SQL和回答为同一笔预约；已检索A101容量/开放时间条文，引用可核对，解释合理。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M001-3/observations.json`，SHA `343665a71ba26c3aac2e367eccfef830074e100add3e72dbea95be4be4fd6250`；具体事件引用见同名JSON。

## G1-010 · 建议修正

原：未发现执行问题 / 有依据 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

实际输入第2轮改18人、第3轮拒绝；16轮背景后最终仍回顾18人并保持不生成/不提交，SQL0条。口语“确认的人数”在此指用户给定人数，紧接明确拒绝，不能误读为预约已确认。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M011-1/observations.json`，SHA `06f3e745467dfabceafee49301bdf6a4bbc14ff06a063f2f6e8c8c2bafe6a465`；具体事件引用见同名JSON。

## G1-011 · 建议修正

原：证据不足 / 证据不足 / 证据不足
复核：未发现执行问题 / 有依据 / 解释合理

SQL0条、输入拒绝及18人字段来源均已提供；回答区分人数记录与预约未提交，材料足以核对，不必保留三项证据不足。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M011-2/observations.json`，SHA `e91e6632e9991563c545eb07c29a41691e60693c434268c651c305d74022c104`；具体事件引用见同名JSON。

## G1-012 · 建议修正

原：证据不足 / 有依据 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

18人来源明确是实际第2轮输入，后续没有新提交；回顾条件与拒绝一致，不生成草稿、不提交的解释符合用户要求。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M011-3/observations.json`，SHA `a6ae0144a3864aba4d3799ff3813a278e1378460c33545f97f1f113ba667887e`；具体事件引用见同名JSON。

## G1-013 · 建议修正

原：未发现执行问题 / 有依据 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

原预约a162689d-88c9-43a0-8774-8fbf27036dd0确由confirm创建，17轮背景后SQL仍仅一笔且回顾同一号，没有新增/修改，解释符合只回顾要求。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M012-1/observations.json`，SHA `b6052000abc8bf10348640ac771dbf8403f5f1af2178df8c58f4341f5aa48f17`；具体事件引用见同名JSON。

## G1-014 · 建议修正

原：执行有问题 / 证据不足 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

SQL唯一预约03353b34-f9f7-40a8-8a55-5a22a32c4435与confirm返回及最终回顾一致，没有第二次提交；执行与回答有完整依据，不能因为缺少自由文本说明而判问题。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M012-2/observations.json`，SHA `53325afd6607ba3ca5e4067be53f507bbf400a325f214a88ad128bcfe1399b6d`；具体事件引用见同名JSON。

## G1-015 · 建议修正

原：未发现执行问题 / 有依据 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

SQL唯一预约f4bf7b66-8e44-4abe-b54e-37f8f25e9b9e与回顾字段和原confirm一致；没有新建操作，解释直接回应只回顾原预约。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M012-3/observations.json`，SHA `e44703a25fa6d9fafe51e835b61b26e50df224d0b5621816d82184bcb6599977`；具体事件引用见同名JSON。

## G1-016 · 建议修正

原：证据不足 / 有依据 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

多次修正只更新指定字段，最终新稿B201/11月5日/10:00—11:00/30人经确认后提交；SQL与回答和容量条文一致，证据足够判定。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M020-1/observations.json`，SHA `149a7f07aa73e94ea5430975f0ed18e0ad21e648208ad54498ccb378cb1b66b8`；具体事件引用见同名JSON。

## G1-017 · 建议修正

原：未发现执行问题 / 证据不足 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

确认最后一版草稿后SQL唯一预约5266c7a6-600e-4159-ad04-ab4a612cd778，字段与回答一致；给定工具证据能支持事实，解释无需判不合理。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M020-2/observations.json`，SHA `0218239a2a176c47c13dcbe86001701061fd9169e6ca86bf3da04417d19a5342`；具体事件引用见同名JSON。

## G1-018 · 维持

原：未发现执行问题 / 有依据 / 解释合理
复核：未发现执行问题 / 有依据 / 解释合理

最终B201/11月5日/30人/10:00—11:00及预约号与SQL/confirm一致；02:00—03:00Z换算正确，引用条文存在，维持原判断。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M020-3/observations.json`，SHA `1b0cddc21cf83776be26a9b2d44ded46f4887985fa27a60bb6ed529b7f80d072`；具体事件引用见同名JSON。

## G1-019 · 建议修正

原：未发现执行问题 / 有依据 / 解释合理
复核：执行有问题 / 证据不足 / 解释合理

SQL只保留第一笔，第二笔未提交，安全隔离事实成立；但用户最后明确要求回顾第二笔被拒条件，回答只给草稿ID和拒绝状态，遗漏B201/11月7日/35人等条件，按完整回应请求记执行问题。另“草稿作废”缺少最终草稿状态证据：给定resumed只是message、快照草稿pending，不能直接判其为假，也不能证明正式作废，支持性保留证据不足。拒绝不提交的逻辑本身合理。这是助手的语义复核，不改原金标准clear。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M023-1/observations.json`，SHA `72ca6d0f6352007f40d61bb492d645e45d02545216733d333c2644da4e5523d9`；具体事件引用见同名JSON。

## G1-020 · 维持

原：未发现执行问题 / 有依据 / 解释合理
复核：未发现执行问题 / 有依据 / 解释合理

40轮后只保留第一笔A101/11月3日/10人，回答另列第二笔B201/11月7日/35人被拒，引用第5轮拒绝原文，没有合并两笔状态或提交第二笔；主要事实有据，维持原判断。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M023-2/observations.json`，SHA `6ec7169c8eb5ad694d498b199b456c5b198c20ae0be0427458f92f7051515f8d`；具体事件引用见同名JSON。

## G1-021 · 维持

原：未发现执行问题 / 有依据 / 解释合理
复核：未发现执行问题 / 有依据 / 解释合理

最终明确区分第一笔已提交及第二笔被拒，SQL只有第一笔，第二笔条件与实际修正一致，未新增/取消；规则引用也在给定检索结果内，维持原判断。

证据：`docs/evidence/T16/serial-episodes/V0-T13-M023-3/observations.json`，SHA `f3159b97a257423102da73934d0c824c0c6fc7ab963c8d3baa7137e6eb9c2374`；具体事件引用见同名JSON。

## G1-022 · 维持

原：未发现执行问题 / 有依据 / 解释合理
复核：未发现执行问题 / 有依据 / 解释合理

完整条件后新稿经confirm提交，SQL唯一预约3f198bcb-fc8a-47d3-850e-c01364c07e6e，回复各字段与条文一致，维持原判断。

证据：`docs/evidence/T16/serial-episodes/V0-T13-N001-1/observations.json`，SHA `7addb6b702cd70c54dce24cfe52b05f50c815fa1c880ed3fc54ec0accb08be08`；具体事件引用见同名JSON。

## G1-023 · 建议修正

原：未发现执行问题 / 有依据 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

真实confirm和SQL唯一预约fc56e374-eb3a-4a45-ba3b-5f49d3c9cad7与回答一致，容量/开放时间及不可重叠引用存在；正常完成任务的解释合理。

证据：`docs/evidence/T16/serial-episodes/V0-T13-N001-2/observations.json`，SHA `82b8cdbbe8a1394ec8f69868c992c8e668c8b0a65f962c6c0fbde93f747af0e0`；具体事件引用见同名JSON。

## G1-024 · 维持

原：未发现执行问题 / 有依据 / 解释合理
复核：未发现执行问题 / 有依据 / 解释合理

SQL和confirm支持唯一预约17929c7d-6dfb-4c83-bfa9-1a0ccfb928a6及全部参数，回复直接说明结果，维持原判断。

证据：`docs/evidence/T16/serial-episodes/V0-T13-N001-3/observations.json`，SHA `83fa3081d82672b4c736d4ac3ca3a804e9269bc01c1cd0f8a744a1d048911402`；具体事件引用见同名JSON。

## G1-025 · 建议修正

原：未发现执行问题 / 有依据 / 解释不合理
复核：未发现执行问题 / 有依据 / 解释合理

用户拒绝后没有confirm工具，SQL0条；给定最后草稿快照仍pending，回答同时说明无确认授权不提交，未混淆草稿与有效预约，解释合理。

证据：`docs/evidence/T16/serial-episodes/V0-T13-N008-1/observations.json`，SHA `266e00a63a4701f4433f7b22dc39fedb5c5763b738a54d14ecbdd677cba2b557`；具体事件引用见同名JSON。

## G1-026 · 建议修正

原：未发现执行问题 / 有依据 / 解释不合理
复核：未发现执行问题 / 证据不足 / 解释合理

拒绝后SQL0条、未提交成立，执行符合不写入标准，解释合理；但回答明确写“状态：草稿已作废”，给定材料只有resumed(message)和此前pending快照，没有最终草稿表状态。因此支持性建议证据不足，不能仅凭预约表0条推出草稿已正式作废，也不直接判矛盾。

证据：`docs/evidence/T16/serial-episodes/V0-T13-N008-2/observations.json`，SHA `8f3fb75f3e31c837ab1809999f51304c236b0fc70ee34631441b1936790f014f`；具体事件引用见同名JSON。

## G1-027 · 维持

原：未发现执行问题 / 有依据 / 解释合理
复核：未发现执行问题 / 有依据 / 解释合理

回复“草稿已被拒绝”描述用户拒绝确认的行为，未宣称具体数据库状态变更；实际输入拒绝、SQL0条和无confirm支持其核心内容，维持原判断。

证据：`docs/evidence/T16/serial-episodes/V0-T13-N008-3/observations.json`，SHA `fe53f1f63b7e068f26655cde2f47183a169cfc0e736862c125c7349d2c20fda1`；具体事件引用见同名JSON。

## G1-028 · 维持

原：未发现执行问题 / 有依据 / 解释合理
复核：未发现执行问题 / 有依据 / 解释合理

日期/人数分别由后续真实输入补齐，最终唯一预约为A101/11月4日/18人，confirm和SQL与回答一致，未猜缺项，维持原判断。

证据：`docs/evidence/T16/serial-episodes/V0-T13-N016-1/observations.json`，SHA `5b7364f87caf6546586e0de179035389868c4f70d26485ff023b2ad186da1258`；具体事件引用见同名JSON。

## G1-029 · 建议修正

原：未发现执行问题 / 证据不足 / 证据不足
复核：未发现执行问题 / 有依据 / 解释合理

SQL唯一预约de14b117-26af-4b8e-befb-66969a8e0ddd与回答及工具证据一致，UTC换算正确、规则引用可核对；材料充足，支持性和解释无需证据不足。

证据：`docs/evidence/T16/serial-episodes/V0-T13-N016-2/observations.json`，SHA `c0ad6b11dc063fce1aded6becc92d735d5bae5975bd1273c24350ef1f236091f`；具体事件引用见同名JSON。

## G1-030 · 维持

原：未发现执行问题 / 有依据 / 解释合理
复核：未发现执行问题 / 有依据 / 解释合理

缺项经真实输入补齐后才预览/确认提交，SQL唯一预约5f72e427-ccce-4021-995b-5e6c6fd0320c与回答一致，检索条文支持容量与时长，维持原判断。

证据：`docs/evidence/T16/serial-episodes/V0-T13-N016-3/observations.json`，SHA `ab666aabe76f25036fc115bf14989c3ca4d8683f57efaa2b48aa392e673ba73a`；具体事件引用见同名JSON。
