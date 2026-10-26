# T02—T06 数据库迁移、种子与隔离操作

日期：2026-10-26，Asia/Shanghai。MySQL 8.0.39已实测。数据库包含12张平台表、4张实验业务表，另有 `alembic_version`；字段/约束见[数据库字典](architecture/database.md)。当前迁移20261026_0003在原0002上增加真实run事件归属、提交序列及索引，execution_id可空、共23个触发器。旧事件回填提交前的既有顺序且仍标1.0，不声称补齐历史采集。

0003在存在run级事件时拒绝降级，避免丢失证据；升级应停写、备份并核对真实DDL，迁移中断不直接stamp。T06实现过程中主库先应用0003、补齐两项索引后，已用alembic check核对metadata；全新临时库按最终迁移完整升降级复验。详情见[T06验收](acceptance/T06.md)与[事件契约](architecture/evidence.md)。下文T02/T04内容为历史操作边界，当前以本段及各任务记录为准。

## 首次初始化与检查

从项目根进入 `backend`，凭据读取根目录忽略的 `.env`，命令行不传密码：

```powershell
Set-Location D:\AgentChaos\backend
.\.venv\Scripts\python.exe scripts/manage_db.py migrate
.\.venv\Scripts\python.exe scripts/manage_db.py seed
.\.venv\Scripts\python.exe scripts/manage_db.py check
.\.venv\Scripts\python.exe scripts/manage_db.py inspect
```

先由数据库管理员创建空应用库，并在 `.env` 填写已有MySQL连接与 `DEMO_SEED_PASSWORD`。`scripts/init_env.py` 仅在 `.env` 不存在时创建本地配置并生成随机凭据；不会修改当前已有文件。已有环境升级时单独填写新增项，不能用示例空值覆盖本地密钥。本机已经按用户提供的本地密码配置，文档和代码不保存它的值。

迁移版本为 `20261025_0001`。种子首次创建 `demo_operator`（operator）、`demo_reviewer`（reviewer）、一个场地预约对象登记、一份实验规则修订，以及 `demo` scope下的A101/B201/C301。对象的能力明确标为 `agent_implemented=false`；不生成虚假运行、用例、Agent版本或预约。账号密码采用随机盐PBKDF2-SHA256、600000轮存储，不是明文。登录鉴权将在相应后续任务实现。

种子可重复运行。账号/规则/场地与现有内容冲突时整次事务回滚并拒绝覆盖；不会通过重跑重置密码。规则修改应新增修订，不能改写已经入库的v1。规则原文位于 [booking-rules-v1.md](../knowledge/booking-rules-v1.md)，SHA256保存在对应知识记录中。这些规则是实验设定。

迁移需要建表、索引、外键、CHECK和TRIGGER权限。当前沿用用户已有的本地开发数据库账号；尚未配置生产最小权限角色。集成测试另需创建/删除临时库权限。

## 初始化或清理单个测试范围

```powershell
.\.venv\Scripts\python.exe scripts/manage_db.py fixture-init --scope test:manual-a
.\.venv\Scripts\python.exe scripts/manage_db.py fixture-init --scope test:manual-a
.\.venv\Scripts\python.exe scripts/manage_db.py fixture-clear --scope test:manual-a
```

初次初始化新增3个场地，再次新增0。只有匹配 `test:[A-Za-z0-9][A-Za-z0-9:_-]{0,58}` 的明确scope可操作；`demo`、空串、通配符和SQL片段会拒绝。清理在同一事务内按slots→bookings→drafts→rooms删除，四次DELETE均绑定同一scope，不关闭外键检查。

已被 `ac_test_executions.scope_id` 登记的scope拒绝初始化和清理，以保留历史证据。T05创建执行时应先在事务内初始化新fixture再登记执行；同一执行恢复继续使用原scope，不能以重新初始化覆盖已提交副作用。当前连接显式使用REPEATABLE READ；清理锁定执行scope索引与场地行，锁持有至调用方事务结束。未来Worker还需租约、可信身份和并发恢复控制，不能仅凭本工具宣称这些能力完成。

真实三组隔离验证见 [scope-isolation.json](evidence/T02/scope-isolation.json)：demo、test:a、test:b分别有3场地/1草稿/1预约/1占用片；清理test:a后它们归零，另两组保持不变。验证用例在临时库事务中构造数据，结束后回滚并删除临时库；主应用库没有残留的测试预约。

## 数据约束与版本保护

- scope/room复合外键覆盖草稿、预约、占用片；预约到草稿还绑定actor。占用片到预约绑定scope和room。`(scope,actor,idempotency_key)`唯一，`draft_id`也唯一，防止换键重复使用同一确认草稿。时间、人数、容量及半小时占用片对齐有CHECK。日期/人数/哈希/有效期、冲突事务和取消行为仍由T03服务补齐并验证。
- 对象版本、知识修订、故障修订、事件、断言结果及产物清单禁止UPDATE/DELETE；用例审核后禁止UPDATE/DELETE。运行允许状态变化，配置/清单/预算不可变；执行允许状态、租约、结果更新，输入/scope/session不可变，attempt不能递减。正式结果更正需追加证据，完整历史追加语义在T05/T06落实。
- 用例批准要求有效reviewer及审核时间；未批准用例不能登记执行。家族划分冲突有数据库触发器前置检查；正式并发审核、同家族串行化及数据集冻结在T11/T13实现，当前不据此宣称并发批准已验证。
- 缺陷首次执行及修复版本必须属于同一对象；事件parent不能跨执行或attempt引用。22个触发器用于普通DML保护；有DDL权限的管理员仍能删除触发器/表，不等同于防篡改审计系统。

时间统一用带时区Python datetime传入，存UTC的DATETIME(6)，读取恢复UTC；拒绝无时区输入。连接时区显式为 `+00:00`。外部SQL写入也必须遵守UTC。JSON赋值先复制嵌套内容，保留中文/emoji并拒绝NaN；可变JSON修改时赋新值，不能依赖原地修改自动保存。预算单位/精度由快照明确，后续结算用整数最小单位，不在T02生成费用或调用模型。

## 迁移验证与故障恢复

T04当前主库为20261025_0002，在既有草稿表增加可空session_id与索引。T03直接调用仍用NULL；T04可信会话ID进入草稿绑定和哈希。同一用户不同会话可保留各自pending。初始种子对象标记agent未实现是T02历史初态；只有真实验证后的V0登记才将对象能力标为T04已实现，并创建不可变版本。当前版本1，模型/代码/知识清单见docs/evidence/T04/frozen-v0.json，不靠重新seed伪造能力。

```powershell
$env:TEMP = 'D:\AgentChaos\.cache\tmp'
$env:TMP = $env:TEMP
$env:AC_INTEGRATION = '1'
New-Item -ItemType Directory -Force ..\.local | Out-Null
.\.venv\Scripts\python.exe -m pytest -q --tb=short --junitxml=..\.local\T02-pytest.xml
.\.venv\Scripts\python.exe scripts/verify_database_state.py --output ..\.local\T02-schema.json --junit ..\.local\T02-pytest.xml
.\.venv\Scripts\python.exe scripts/export_database_docs.py
```

`AC_INTEGRATION=1` 会连接当前配置的MySQL/Redis，数据库测试仅在随机命名的 `agentchaos_t02_verify_<随机后缀>` 临时库中写入。迁移测试执行upgrade→downgrade→upgrade并检查metadata；finally删除该临时库，不对主应用库执行回退。需要保存本次隔离实测证据时，额外设置 `AC_T02_EVIDENCE_DIR` 为专用输出目录；不要覆盖已归档验收证据。

MySQL DDL不能依赖整个迁移事务自动回滚。若首次迁移中断，先停写、备份并核对真实表/触发器和Alembic版本；新建空库重建是开发空库的恢复方式，已有数据必须另写修复迁移。不要直接 `stamp head` 掩盖未完成结构。`downgrade base` 会删除16张表及全部数据，仅允许在明确的一次性临时库或已确认可销毁的库中使用。

Alembic自动比较不覆盖触发器、所有CHECK变化或全部服务语义；变更时手工审阅迁移并运行实际约束反例。只读核查脚本校验声明的约束/索引名称存在，并输出真实结构、行数和触发器定义哈希。它不会验证每个触发器的所有并发路径。本次结果见 [T02验收记录](acceptance/T02.md)。
