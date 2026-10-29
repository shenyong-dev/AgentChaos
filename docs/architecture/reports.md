# T15 缺陷与报告契约

只实施已授权T15，不运行T16正式实验。报告取持久运行/事件/断言/SQL快照，不调用模型或预约工具。人工复核使用所属operator账号，记录实际操作者，不能称为独立盲审；不修改AI、规则结果或历史轨迹。

缺陷候选从规则failed及AI failed分别生成；故障生效、取消、平台错误、证据不足不自动算Agent缺陷。按目标、用例修订/预期、断言与故障条件归并，保存每个出现位置。人工确认/拒绝/标记修复追加审计artifact，保留原自动信号；verified另需同条件、修复版本的匹配通过执行和证据。

比较固定用例内容/重复数、seed、时钟/fixture、故障、限额、预算、模型配置、知识和评价器条件；列出所有不一致/缺失项，不能默认有效。被测目标版本差异单列为整体版本因素，不解释单因素因果。开发运行条件匹配不代表正式V0/V1实验或统计显著。

指标分别给出计划执行数、运行状态、有效规则分母及通过/失败、证据不足/错误/取消排除、任务达成、AI辅助、尝试次数和费用/unknown；原始计数与比率同时输出，零分母不编造百分比。

JSON/Markdown/ZIP由显式POST生成，request_id幂等且不刷新有效期；生成时冻结报告器版本、来源、原始结果和人工复核快照。证据包包含事件、断言、SQL与用例/知识/配置、原始trace artifact、文件SHA256清单和离线校验脚本。下载逐次认证归属、有效期、路径与字节哈希；过期410，越权404，损坏503。ZIP不包括历史报告，禁止递归打包或读取任意本地路径。

所有新增数据在现有ac_defects/ac_artifacts；文件位于D盘项目.local/reports，按数据库与运行隔离。工作台区分浏览历史证据、生成文件与新执行；报告页没有自动重跑入口。16表、23触发器与0006迁移保持。

## HTTP接口

统一前缀`/api/v1`，Basic认证沿用本地工作区凭据。仅operator可操作自己的对象/运行，reviewer返回403，其他operator访问外部资源返回404。GET不生成文件或候选。

| 方法与路径 | 行为 |
| --- | --- |
| GET /reports/runs/{UUID} | 读取终态运行的报告快照、来源、分母和缺陷复核 |
| GET /reports/compare?left={UUID}&right={UUID} | 双方条件逐项核对、配对执行与各自计数 |
| POST /reports/runs/{UUID}/defects | 幂等归并当前最新规则/AI失败候选 |
| GET /defects?cursor=0&limit=20&status=open | 当前账户的候选分页，limit上限100 |
| GET /defects/{key} | 出现位置、原始断言、审计历史与revision_hash |
| POST /defects/{key}/reviews | 显式人工决定，追加独立审计文件 |
| POST /reports/runs/{UUID}/exports | 一次生成JSON/Markdown/ZIP三份冻结文件 |
| GET /reports/exports/{artifact_id}/download | 当前授权、有效期、数据库目录、长度及SHA校验后下载 |

请求模型以`openapi.json`的`ExportReport`、`DefectReview`为准。导出请求含UUID request_id和expires_hours（1—168，默认24）；返回三个artifact的id/kind/sha256/bytes/expires_at/download。下载带`X-Content-SHA256`和`nosniff`，返回已校验的同一字节缓冲，避免读后再读路径造成竞态。

复核含UUID request_id、64位expected_hash、decision、severity、非空note、human_attestation=true，fixed/verified另需fixed_version_id，verified再需verification_execution_id。旧hash冲突、非法跳转或错误修复证据409；同request_id不同内容409。同一成功请求重发返回原审计或原导出，不延长有效期。

状态顺序：open→confirmed/rejected；confirmed→fixed/rejected；rejected→confirmed；fixed→verified/confirmed；verified→confirmed。verified验证规则断言和证据，不推断AI语义信号已经修复。严重度初始medium仅是候选暂定值，根因保持空，人工决定有实际actor_id和时间。

## 快照与文件边界

读取时核对运行manifest哈希、目标版本及其配置哈希、冻结执行数与事件连续序列；规则有效性再按证据完整性判断。报告保留最新规则与AI各自版本的计数，也保留所有原始断言。有效规则分母仅包含证据完整且最新规则明确passed/failed的执行；未评价、inconclusive、error、取消和缺证据分别列出。任务达成分母只计明确fulfilled/not_fulfilled，未知不是失败或成功。usage和timing保持原记录，估算不是账单，不把未知补成0。

JSON与Markdown格式为agentchaos-report-v1，包含reporter、run、target、executions、statistics、defects、knowledge和source_event_count。报告器T15-v1另保存实际报告模块的SHA256。Markdown将来源数据放入足够长的代码围栏，避免用户原文逃逸围栏。

ZIP包括report.json、report.md、frozen/run-manifest.json、frozen/knowledge.json、evidence/events.json、evidence/executions.json、原始trace/judge artifact、关联复核审计、README.txt、verify.py及manifest.json。最多64MiB，超限413；冻结知识或原始来源文件缺失/篡改503，不能假装完整。清单列出成员长度/SHA256，核对精确成员集且拒绝危险路径；自带清单只证明内部一致，来源还需核对认证下载记录的外层ZIP哈希。

原报告是生成时快照，后续复核不回写；再次显式生成会得到新的artifact。文件写入或事务失败只清理本次新建的随机文件，不递归删除目录、不触及历史证据。

## 验证与限制

T15实测240项后端、最终10项专项、5项真实UI加1项预览返回专项，以及5项既有非付费页面检查通过。专项覆盖并发幂等、原判定保留、比较条件、实际修复断言/SQL、过期、文件损坏、权限和离线清单脚本。核对阶段隐藏输入表单，返回重开清空说明/声明/预览；不自动保存。最终离线策略夹具配真实MySQL/Redis/预约工具，明确不是真实V1或正式实验。来源、失败修复、完整步骤见`docs/acceptance/T15.md`与`docs/evidence/T15/`。

本轮没有PDF、无需新依赖、没有新增模型调用或T15账本。只追加源码兼容V0版本40，原39版本及T13包不变；正式V0/V1、统计检验与模型质量结论仍待后续获准任务。
