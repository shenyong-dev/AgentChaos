# T01—T03 环境与复现说明

环境验证日期：2026-10-24—2026-10-25，Asia/Shanghai。新增程序、虚拟环境、下载及缓存放在 D 盘。未创建 WSL 发行版；未运行 Docker 安装或容器。下方外部价格与文档快照的核对日期为2026-10-24。

## 已验证的本机环境

| 项目 | 实际结果 |
|---|---|
| Windows | Windows 11，x64 |
| Python | 3.12.6；项目虚拟环境在 `D:\AgentChaos\backend\.venv` |
| Node / npm | 24.11.0 / 11.6.1；npm 缓存在项目 `.cache/npm` |
| uv | 0.12.21；缓存指定为项目 `.cache/uv` |
| 前端 | Vue 3.5.44、Vite 8.3.5、Vue Router 5.4.0、Pinia 4.1.0、Element Plus 2.14.7 |
| MySQL | 已有 MySQL80 服务，8.0.39，`127.0.0.1:3306`；T02已迁移16张业务表及Alembic版本表，种子数据已装入 |
| JSON/Search 端点 | 原生 Windows Memurai 8.2.0，Redis 协议版本自报 8.2.10，`127.0.0.1:16380` |
| 模块 | JSON：ReJSON，ver=80001；Search：ver=80213；真实 JSON 索引查询通过 |
| 检查点 | 真实 RedisSaver 写入、关闭重开读取、服务端重启后读取均通过 |
| Docker | 命令与 Desktop 不可用；已有 docker-desktop WSL 注册指向缺失磁盘，未修复或删除 |
| 模型 | 已读取本地 API Key；2026-10-25 鉴权请求 `GET /models` 返回 200，三个角色的 deepseek-flash 均可用；文本生成请求为 0，付费调用仍关闭 |

Python 完整依赖由 `backend/uv.lock` 锁定，前端由 `frontend/package-lock.json` 锁定。主要后端版本：FastAPI 0.162.0、Pydantic 2.14.1、SQLAlchemy 2.1.6、PyMySQL 1.2.3、Redis Python 6.4.0、Celery 5.6.3、LangGraph 1.2.17、langchain-deepseek 1.1.1、langgraph-checkpoint-redis 0.5.2。

T03补充：Windows时区包tzdata作为直接依赖显式声明，仍使用原锁中的2026.5；离线lock/sync未升级其他依赖、未新增下载，90个已安装包声明兼容。数据库连接保持UTC/REPEATABLE READ，新增InnoDB锁等待5秒；预约服务命名锁等待3秒。已有MySQL/Redis地址及密码仅在本地配置。

## T03确定性预约入口

API文档 `http://127.0.0.1:8000/docs` 已提供5个预约接口：查询、草稿、确认、按请求查证和取消。Authorize使用已有demo_operator及本地DEMO_SEED_PASSWORD，后端绑定身份与demo scope；前端环境页面仍在5173，不把它当作预约或Agent界面。详见[接口与请求示例](architecture/booking.md)。

T03最终后端74项通过；8并发相同请求仅1预约、4身份竞争仅1成功、事务回滚及提交回执丢失后查证均已验证。主库网络烟测只做认证/查询/查证，预约相关行数未变；写入测试在临时库内完成并删除临时库，证据见[T03验收](acceptance/T03.md)。本项未发起模型请求或运行Worker。

## Windows Redis 模块与启动

用户指定模块目录为 `D:\RedisJson`、`D:\RedisSearch`，认证采用用户提供的本地设置；密码不写入仓库。

原 `D:\redis` 是 Cygwin Redis 8.10.2：能 PING，但未带 JSON/Search。现已在它的 `redis.conf` 中加载 `D:\RedisJson\rejson.dll`，JSON.SET/GET 通过；配置备份在 `D:\RedisJson`。其 6379 端口继续可用。

Memurai Search DLL 在 Cygwin Redis 上能加载，但 FT.CREATE 导致临时测试实例崩溃；匹配版本的 JSON/Search DLL 配对也未能通过该实例的索引创建。因此没有把 Search 写入原 6379 服务的启动配置。

实际给本项目使用的原生 Windows 配套组合：

| 文件/目录 | 用途 |
|---|---|
| `D:\RedisJson\rejson-memurai-8.2.0.dll` | 与配套服务端一起验证过的 JSON 模块 |
| `D:\RedisSearch\redisearch.dll` | Search 模块 |
| `D:\RedisSearch\memurai.exe` | 配套 Windows 服务端，未执行 MSI 安装、未注册系统服务 |
| `D:\RedisSearch\memurai.conf` | 监听本机 16380，包含认证、两个模块与持久化配置 |
| `D:\RedisSearch\data-candidate` | Linux 环境未使用；这是 Windows 实例的实际 AOF/RDB 数据目录 |
| `D:\RedisSearch\memurai-license-dev.txt` | 厂商随包提供的开发许可证文件，不纳入 Git |

启动：在项目根执行 `powershell -File scripts/start-redis.ps1`。当前为隐藏后台进程，重启 Windows 后需重新启动。停止前保存数据，不能靠删除目录停服务；不得对已有 Redis 执行 FLUSHDB/FLUSHALL。

该运行时采用[Memurai 开发版](https://www.memurai.com/get-memurai)，用于开发/测试，最长连续运行 10 天后需重启。竞赛运行前安排受控重启并验证检查点恢复；正式生产部署不能沿用开发版授权。厂商程序、许可证与模块二进制不打包进源码仓库，按厂商许可另行获取。

下载来源：[RedisJSON Windows 移植](https://github.com/zkteco-home/RedisJson)、[Memurai Windows 下载](https://www.memurai.com/get-memurai)。后者走页面公开的下载链接流程，未注册账号或提交个人信息。MSI 和 Search DLL 的 Authenticode 签名验证为 Valid，发布者 Janea Systems。SHA256 和来源记录保存在两个模块目录的 download-manifest.json 与项目证据记录中。

## 开发环境重建

在 PowerShell 中从项目根执行；命令不会覆盖已有 `.env`：

```powershell
New-Item -ItemType Directory -Force .cache\tmp | Out-Null
$env:TEMP = "$PWD\.cache\tmp"
$env:TMP = "$PWD\.cache\tmp"
$env:UV_CACHE_DIR = "$PWD\.cache\uv"
$env:npm_config_cache = "$PWD\.cache\npm"
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.cache\playwright"
python scripts/init_env.py
Set-Location backend
uv sync --locked --python 3.12
Set-Location ..\frontend
npm ci --no-fund --no-audit
Set-Location ..
```

填写根目录 `.env` 的已有 MySQL 地址/端口/账号/密码与应用库名、原生 Redis 端点 `127.0.0.1:16380` 及认证。本机已完成填写；仓库 `.env.example` 只保留占位。不要把后端密钥放入 VITE 环境变量。

T01只创建空的agentchaos库并查询连通性；T02现已完成16表迁移、演示账号及规则种子。其他机器先创建空库并填写连接和 `DEMO_SEED_PASSWORD`，再依次运行 `scripts/manage_db.py migrate`、`seed`、`check`（在backend目录）。账号尚无登录接口；完整步骤、scope清理和回退限制见 [数据库操作](database-operations.md)。

启动原生 Redis 后执行 `powershell -File scripts/start-dev.ps1`；前端 `http://127.0.0.1:5173`，API `http://127.0.0.1:8000/docs`。停止前后端使用 `powershell -File scripts/stop-dev.ps1`；该脚本不会停止 MySQL 或 Redis。

## 验证命令

```powershell
Set-Location backend
uv run --locked ruff check app tests scripts
uv run --locked ruff format --check app tests scripts
$env:AC_INTEGRATION = '1'
uv run --locked pytest
uv run --locked python scripts/verify_integrations.py
uv run --locked python scripts/export_contracts.py
Set-Location ..\frontend
npm run build
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\AgentChaos\.cache\playwright'
npx playwright install chromium
npx playwright test
```

AC_INTEGRATION=1才执行真实MySQL/Redis集成检查；T02新增数据库用例会在随机命名的临时库中执行写入及回退，结束后删除临时库，需相应权限。T02全量后端38项通过；首次因Redis未启动出现1项失败，按既有D盘配置启动后补跑及全量复跑通过，详见 [T02验收](acceptance/T02.md)。浏览器前两项使用真实服务，第三项故意阻断健康请求以验证错误态；不把模拟错误测试当作真实故障实验。`docs/evidence/T01`保留原T01证据，T02结构与隔离证据另在`docs/evidence/T02`。

## T04当前状态，2026-10-25

用户已批准T03并授权T04，agent本轮累计上限1美元，生成器/裁判仍0。本地付费开关启用并由跨进程账本限制；不是每次重启获得新额度。真实DeepSeek非思考生成、工具调用/回传、流式及usage已验证，10类真实开发对话和96项后端回归通过，见[T04验收](acceptance/T04.md)。累计占用保守估算0.468625美元，含未知超时预留，不是实际账单。

健康页展示与当前本地凭据/模型配置匹配的成功调用历史，明确显示时间；刷新不调用模型，也不保证此刻服务商可用。六个会话接口在/api/v1/demo/sessions下，Swagger可操作；未新增聊天工作台，批量执行仍未开放。主库迁移20261025_0002，仍16表/22触发器、无测试预约，V0版本2已登记。T05未获准，Linux Worker条件尚待落实。

以下模型列表与T01叙述保留为历史探测背景，当前能力/授权以上述T04记录为准。

## 模型方案与历史未验证事项

官方端点为 `https://api.deepseek.com`，三角色分别读取 AGENT_MODEL、CASEGEN_MODEL、JUDGE_MODEL，候选均为 deepseek-flash；独立输出上限与预算。ALLOW_PAID_MODEL_CALLS 默认为 false，预算 0 表示没有授权，不表示不限额；T01 未实现执行模型请求的接口。

2026-10-25 用户写入根目录 `.env` 后要求测试连接：实际携带凭据请求官方 `GET /models`，HTTP 200，返回 `deepseek-flash` 与 `deepseek-v4-pro`。三个角色当前配置均在列表中，证据见 `docs/evidence/T01/deepseek-connection.json`，仅保存脱敏结果。此结果验证网络、密钥鉴权及模型列表，不代表文本生成、工具调用或账户余额已验证。健康接口不会自动请求模型；修改 `.env` 后需重启 API 读取配置，页面显示“已配置凭据，未发起模型请求”仍属于预期行为。

按 2026-10-24 [DeepSeek 官方入门](https://api-docs.deepseek.com/)及[价格页](https://api-docs.deepseek.com/quick_start/pricing/)记载，deepseek-flash 当前由 DeepSeek-V4.1-Flash-1017 提供，另一型号为 deepseek-v4-pro。别名可漂移，T04 保存请求/响应标识并真实验证工具调用、多轮、流式和 usage。

Flash 每百万 token 的美元价格快照：输入命中缓存高峰 0.006/低峰 0.003，输入未命中高峰 0.30/低峰 0.15，输出高峰 1.20/低峰 0.60。价格会变化，正式实验前重查，不当作实际账单。[官方定价](https://api-docs.deepseek.com/quick_start/pricing/)

官方账户并发上限记载 Flash 2500、Pro 500，但本项目起始并发仍为 1，团队账户余额、配额和三角色预算待登记。[官方限流说明](https://api-docs.deepseek.com/quick_start/rate_limit/)

非思考模式使用 `extra_body.thinking.type=disabled`。不沿用集成文档的旧型号示例作为当前能力结论，T04 再实际验证 SDK 兼容和工具结果回传。[官方工具调用](https://api-docs.deepseek.com/guides/tool_calls/)、[思考模式及参数边界](https://api-docs.deepseek.com/guides/thinking_mode/)

## Compose 与 Worker 边界

compose.yaml 是五服务部署草案，镜像按 2026-10-24 官方仓库 digest 固定；已验证 YAML 结构，未执行镜像构建或启动。当前本机部署采用用户授权的原生 Windows 方式，不以草案替代实测。

MySQL/Redis 的 Compose 数据绑定到项目 `.local/compose`，使本机数据位置明确位于 D 盘；不得用删除目录代替受控停机。容器方案只用于具备 Docker 的后续环境，不会在当前主机自动建立 WSL。

Worker 在 worker profile 中，T01 仅完成配置及导入验证，没有业务任务。Celery 官方不支持 Windows，T05 前需落实团队同意的 Linux Worker 运行主机或容器；本轮未在 Windows 运行 Worker，也未声称恢复或队列可靠性通过。[Celery 官方说明](https://docs.celeryq.dev/en/stable/faq.html#does-celery-support-windows)

## 团队待补登记

学校/组别、队员姓名与分工、校赛更早截止、官方群对赛程矛盾的确认仍待团队补充。T04已落实agent本轮1美元，生成器/裁判0，未获后续任务新预算；密钥仅在本地。正式实验前按对应任务重新确认适用授权和余额。
