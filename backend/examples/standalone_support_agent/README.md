# 售后工单分流 Agent

规则式 Agent 记录会话内的订单编号和问题类型，为商品破损或物流查询给出处理建议。演示使用虚构订单，不连接真实订单系统。换订单号时会重新询问问题。`triage.py` 提供领域逻辑，`server.py` 提供 HTTP 接入和 SQLite 回执。两者只需 Python 标准库。

在进程环境中设置 `AGENTCHAOS_EXTERNAL_SUPPORT_TOKEN` 后，可从仓库根目录运行以下命令。选择尚不存在的 SQLite 文件，并提前创建其父目录。

```powershell
python backend/examples/standalone_support_agent/server.py --port 18064 --database .local/support-agent-new.sqlite3
python -m unittest discover -v -s backend/examples/standalone_support_agent
```

用 `--resume` 可重新打开已有台账。演练丢失回执时，可用 `--drop-on-text` 指定一条测试消息。首次命中会先保存回执，再省略本次 HTTP 响应，之后仍可按原键查证。

Agent 监听回环地址。按[外部登记模板](../../../docs/design/external-agent/runtime/registry.example.json)登记，服务地址用 `http://127.0.0.1:18064/agentchaos/v1`，能力卡用 `server.py` 内的 `CARD`。凭据环境变量名称用 `AGENTCHAOS_EXTERNAL_SUPPORT_TOKEN`，平台和 Agent 的进程设置相同的值。由项目方自建的示例的 `author_relation` 为 `project_example`。
