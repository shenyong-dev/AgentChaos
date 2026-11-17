# 接入外部对话 Agent

首版使用 AgentChaos 自己的 `agentchaos-http-1` 协议。已有 HTTP Agent 可加一层转换服务。工作台可执行多轮对话、核对回答是否包含指定文本、查证原请求和导出报告。业务事实、内部工具轨迹、检索覆盖与 AI 裁判留待能力扩展。

## 在网页接入

从「对象与版本 → 接入外部 Agent」打开弹窗，选择已批准服务地址，填写能力卡中的服务名称、版本及单次超时。点击「检查连接」，看到通过提示后再点击「确认登记」。用例可点「创建测试用例」直接填写。

维护者将 `EXTERNAL_AGENT_WEB_POLICY_PATH` 指向[接入策略文件](web-policy.example.json)的绝对路径，替换示例域名和固定 IP。在 API、Worker、Dispatcher 为已批准地址提供同名凭据和 `EXTERNAL_AGENT_CALLS_ENABLED=true`。策略目录专用于外部接入，三个进程共享。API 需要目录写权限，受管 Docker Worker 和 Dispatcher 只读挂载该目录。不同数据库和执行命名空间使用独立的连接记录。服务配置保存后若版本登记失败，按表单原配置重试。更换批准的目标地址时使用新的地址标识。

## 使用固定登记文件

1. 维护者参照[登记文件](registry.example.json)配置固定 URI、固定 IP 和能力卡。将 `EXTERNAL_AGENT_REGISTRY_PATH` 指向该文件的绝对路径，并在 API、Worker、Dispatcher 配置中显式设置 `EXTERNAL_AGENT_CALLS_ENABLED=true`。服务凭据通过条目指定的环境变量注入，不写入登记文件。
2. 操作员在「对象与版本 → 接入外部 Agent」选择已配置服务，检查连接后登记版本。点击「创建测试用例」，逐轮填写消息，再填写最终回复应包含或不得包含的文本。也可以导入[对话用例 JSON](case.example.json)。保存后由审核员核对并批准。
3. 操作员打开已批准的用例，点击「使用此用例新建运行」。向导带入用例和对应对象，完成预检后由操作员确认发起。
4. 在「结果概览」查看逐轮消息与回答，通过「查看文本判定」核对用例预期。在「链路诊断」展开准备会话、逐轮请求和取消记录。执行结束后若原请求待查，可点「查证原回执」。查证沿用原请求标识，补录回执保留原执行结论。

Docker Worker 管理器以只读方式挂载同一登记文件，凭据按环境变量名称继承。容器网络必须能到达所登记的 IP。Windows 主机回环地址在容器中会指向容器本身。使用项目免费的回声示例时，API 与执行器可在同一台 Windows 主机进程中运行。部署用镜像须通过现有依赖锁校验。服务端版本、固定 IP 或能力卡修改后，按新版本重新登记。

`backend/examples/external_agent/server.py` 是带 SQLite 回执的项目示例。在 `backend` 目录、进程环境已设置 `AGENTCHAOS_EXTERNAL_DEMO_TOKEN` 时，可运行以下命令。台账使用新的路径。

```powershell
.venv/Scripts/python.exe -m examples.external_agent.server --port 18056 --database ../.local/external-demo.sqlite3
```

所有请求通过登记的同一个 URL 使用 HTTP POST、JSON 与 Bearer 鉴权。返回 HTTP 200 时，响应必须回显相同 `protocol`、`revision`、`operation`、`request_id` 与 `binding`，并用响应 `data` 替换请求数据。收发上限各为 64 KiB。平台通过固定 IP 建连，HTTPS 仍校验登记主机名。请求绑定包含租户哈希、测试范围和会话标识。

| 操作 | 请求 `data` | 响应 `data` |
|---|---|---|
| capabilities | 空对象 | 登记所用完整能力卡 |
| prepare | 空对象 | `ready: true`、`input_hash` |
| execute | `text`、`expected_revision`、`limits` | `state`、`task_id`、递增一位的 `revision`、`assistant_text`、`input_hash`，可选 `usage` |
| lookup | `original_operation`、`input_hash`，信封沿用原请求键 | `status: found / not_found / unknown`、对应操作、哈希及 `result` |
| cancel | `original_request_id`、`input_hash` | `status: requested / confirmed / already_terminal / not_supported` |

`execute.state` 为 `completed`、`waiting_input`、`working`、`failed` 或 `cancelled`。原实现的规范化 JSON 为 UTF-8、键排序、紧凑分隔、保留 Unicode，`input_hash` 是该编码下请求 `data` 的 SHA-256。完整字段约束见 [协议类型](../../../../backend/app/external/schema.py)。

服务端应在相同绑定内持久关联原键与正文哈希。同键同正文复用回执，同键改正文拒绝。`cancel_before_execute: true` 要求先到达的取消可按原键与哈希阻止尚未处理的输入。平台取消状态和远端取消回执分别记录。丢失回执可查原键，查询仍未知时暂停，平台不会换键发送原输入。远端计费声明由服务方维护，平台保守记录每次请求的声明上限。
