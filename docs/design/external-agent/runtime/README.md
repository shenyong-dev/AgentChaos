# 接入外部对话 Agent

已有 Agent 可通过通用 HTTP 或 Dify 接入工作台。用例可核对回复、检索命中、工具调用与业务状态。测试结果支持正常组和故障组对照及报告导出。

## 在网页接入

在「外部 Agent 接入 → 接入 Agent」选择接入方式。普通账号可以登记自己的 Agent、建用例和运行，数据按账号隔离。

- **通用 HTTP** 接受公网 HTTPS 的 JSON POST 接口和 Bearer 令牌。支持把消息写入指定字段，从 `data.answer` 等路径读取回复。多轮可以沿用远端会话 ID，或发送由 `role`、`content` 组成的消息历史。填写服务地址后，按提示将 TXT 记录添加到域名管理面板，再登记服务及字段映射。返回字段随第一次运行验证。
- **Dify** 填写支持 Chat API 的应用地址和应用密钥。预设读取 `answer`、`conversation_id` 和 `metadata.retriever_resources`。使用 Dify Cloud 默认地址时，登记通过应用参数接口核验密钥。自托管域名按页面提示完成 TXT 验证。用例运行通过 SSE 获取完成的回复。应用有必填变量时，可以在附加字段填写 `inputs`。 [Dify Chat API](https://docs.dify.ai/en/api-reference/chat-messages/send-chat-message) · [Dify 流式响应](https://docs.dify.ai/en/api-reference/guides/streaming)
- **AgentChaos v1** 适用于已经实现本文后面协议的服务。名字和服务版本须与能力卡一致。

上线时先执行数据库迁移 `20261119_0008`，再在 API、Worker、Dispatcher 的私有配置中设置同一个 `EXTERNAL_AGENT_SELF_SERVICE_KEY`（由 `Fernet.generate_key()` 生成）、`EXTERNAL_AGENT_CALLS_ENABLED=true` 及绝对路径 `EXTERNAL_AGENT_WEB_POLICY_PATH`。策略可从 `{"profiles":[]}` 开始。三个进程共享持久目录，API 对目录可写。备份加密配置和密钥；重启时继续使用原密钥。每个用户无需维护者批准、改服务器配置或导入 JSON。

地址和版本相同的登记可重试。更换地址、令牌、固定公网 IP 或字段映射时，用新版本重新登记。测试外部付费服务时，平台按声明上限记录费用。

## 添加证据断言

在「创建测试用例」填写输入，按测试目标添加检查。用例要求的证据缺失时，该项显示「证据不足」及具体原因。

| 检查 | 配置与取证 |
|---|---|
| 检索命中 | 填文档 ID、前 K 条及对话轮次。通用 HTTP 在接入表单配置有序来源数组，例如 `metadata.documents`，以及单项 ID 路径。Dify 使用预设来源字段，也可以使用已关联的 OTLP 检索轨迹。 |
| 工具顺序与参数 | 在「轨迹设置」复制接收地址和 Bearer 令牌。测试服务延续请求的 `traceparent`，上报所属服务 span 和工具子 span。工具 span 使用 `gen_ai.operation.name=execute_tool`、`gen_ai.tool.name` 和 `gen_ai.tool.call.arguments`。用例填工具顺序及需要核对的参数。 |
| 只读业务状态 | 配置同站单独的 GET 路径。请求带 `request_id` 和 `session_id`，返回须回显请求 ID，并提供从业务存储查询的字段，例如 `write_count`。执行前后分别保存快照。 |

轨迹接收为 OTLP/HTTP JSON。使用 OpenTelemetry Collector 时，设置完整的 `traces_endpoint`、`encoding: json`、`compression: none` 和 Bearer 鉴权。需要核对的工具参数由用例指定，只有这些字段会写入轨迹证据。 [Collector HTTP Exporter](https://github.com/open-telemetry/opentelemetry-collector/blob/main/exporter/otlphttpexporter/README.md)

## 对照运行

1. 在已保存的用例上点击「使用此用例新建运行」，先跑无故障控制组。
2. 从正常组结果点「复制为回执超时组」。平台发送一次最后一轮请求，在测试侧模拟回执丢失。测试工具支持故障请求时，也可选择「工具故障」，服务端在测试环境处理 `X-AgentChaos-Test-Fault: tool_error`。平台用工具错误轨迹确认故障是否触发。
3. 在故障组的「链路诊断」点击「查证原请求」读取业务状态。查证是只读操作，沿用原请求 ID，补录结果与原判定一同保留。打开「与来源运行对照」检查相同对象版本和用例下每项判定的变化。

独立工单测试服务提供通用 HTTP、Dify SSE 和 SQLite 写入计数，适合本地核对以上流程。Dify 路径使用本地协议样例。 [工单测试服务](../../../../backend/examples/external_http/server.py)

## 平台共享服务

共享服务仍可在弹窗里选择已批准地址，手动检查并登记版本；共享用例沿用原审核流程。

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
