# T17F 平台身份与浏览器会话契约

2026-11-03。本项复用现有 MySQL `ac_users` 与原生 Redis，不新增表、迁移、依赖、模型调用或权限。T17D通过/继续指令来自本次用户“开始今天任务，进行下一步”；本项完成后停在团队检查，不自动进入T17G。

## 认证路径与权限矩阵

| 入口 | 原认证路径 | 本项浏览器路径 | 保持的权限 |
| --- | --- | --- | --- |
| 工作区、对象、开发库、运行创建 | case_identity | 同一平台会话 | operator；运行/对象仍按原归属 |
| 预约、Agent、交互、执行、证据、故障、规则断言、变形 | booking_identity | 同一平台会话 | operator；其他用户的运行、会话仍不可读写 |
| 生成候选 | case_identity | 同一平台会话 | operator生成自己的候选，reviewer审核；原分页/归属不变 |
| 校准、数据集、独立语义复核 | judging.identity | 同一平台会话 | 原operator/reviewer分工、人工标签与封存限制不变 |
| 缺陷与报告 | report_identity | 同一平台会话 | operator和原运行归属；既有复核声明/转移条件保持 |
| 环境、存活、公开配置 | 无需登录 | 继续公开只读 | 不因此取得业务权限 |
| 历史只读恢复实例 | 显式Basic＋只读HTTP/SQL限制 | 保留原恢复契约 | 不通过新会话打开历史写入口 |

统一登录不会授予审核角色。operator在候选页只能查看自己的候选，不能通过/修改/拒绝候选或提交独立真人标签；reviewer进入operator工作区收到403，仍保持登录并可去审核页，不循环要求输入密码。后端每次读取真实用户的enabled、role和密码哈希指纹；停用、改角色、改密码会使已有会话失效。

## API与凭据

- `POST /api/v1/auth/login`：JSON username/password；成功返回当前身份、角色、CSRF和两个期限，设置不透明Cookie。错误密码401，不回显提交值；额外字段或越界参数400。
- `GET /api/v1/auth/session`：按Cookie恢复身份和CSRF，刷新可续用有效会话。失败401；存储不可用503，禁止降级到信任客户端身份。
- `POST /api/v1/auth/logout`：来源和CSRF校验通过后先删除服务端会话，再删除Cookie。已登出令牌重放401。网络/存储错误时前端说明退出未确认，保留可重试入口，不声称服务端已关闭。

Cookie名包含MySQL host/port/database和executor_namespace的部署摘要，路径`/api/v1`，不设置Domain，HttpOnly、SameSite=Strict。生产必须HTTPS并设置Secure；开发HTTP允许本地测试；HTTPS开发同样设置Secure。当前尚未实施生产TLS部署，HTTPS属性由测试传输验证，不冒充生产环境验收。

随机会话ID为32字节安全随机数的URL安全编码，Redis键只保留SHA256；Redis会话记录保留用户ID、角色、原入口、认证指纹、随机CSRF和期限，不存密码、Basic头或原会话ID。Cookie和CSRF均不得进入URL、日志、截图或交付证据。Redis沿用现有本地访问限制；Cookie防止脚本读取不能代替XSS治理。

闲置30分钟，绝对8小时，不提供永久登录。Redis Lua原子检查期限并续期至两个期限中较早者，退出和续期并发不能复活已删除键。页面可见且最近60秒内有实际键盘/指针操作时，每30秒续验；纯后台轮询和SSE自动重连不续闲置时间。长事件流逐轮重新检查会话且不续期，失效发`session.expired`并结束。运行本身不会因浏览器退出自动取消，原queued/运行数据和已发生副作用保持。

登录尝试计数5分钟：单账号12次、单来源40次，均使用摘要键，返回429；本项不扩大为完整互联网账号系统、注册或密码重置服务。

## 来源与写请求

浏览器请求携带`X-AC-Origin`，须等于服务端看到的scheme/Host和会话原入口；如果存在Origin也须精确相同。写请求额外强制Origin、`X-CSRF-Token`同步令牌；拒绝null来源、其他端口和cross-site/same-site Fetch Metadata。无跨站CORS授权。反向代理须保留外部Host，HTTPS终止须只向受信ASGI代理传递scheme；不自动信任X-Forwarded-Host。

Cookie不按端口隔离，因此不能仅依赖SameSite。部署摘要、精确入口绑定、浏览器same-origin凭据模式和自定义头共同阻止不同端口/恢复实例隐式沿用身份。该方案是同源模块共享登录，不是跨站单点登录。

## 前端生命周期

所有业务HTTP、下载、断言写请求与SSE经同一sessionFetch；不再在页面构造Basic头。兼容的basicHeader仅供旧测试/显式脚本使用，不作为当前浏览器认证路径。当前身份源仅在内存保存身份和CSRF，authorization旧接口现在只提供不含秘密的视图代号。

401统一清身份并要求登录；403保留身份、显示服务端权限原因。登录、退出、账号切换递增身份代次，取消进行中的请求/SSE，重建路由内容；旧请求头、响应体和事件块都验证代次。清空工作区选择/待创建运行、预约未提交文本与草稿视图、交互请求标识、复核表单及下载上下文，不删除服务端事实。跨标签通过BroadcastChannel传递“身份改变”通知，不传令牌；不支持时在重新聚焦及后续请求重新核验。后台标签无法读新用户的数据来恢复旧视图。

## 历史兼容、迁移与回退

三条原认证依赖保留显式Basic优先；旧脚本仍用自己的账号/密码，不依赖Cookie、Redis浏览器会话或CSRF。Basic未通过不能退回有效Cookie。无浏览器头/无Cookie的旧客户端保留原401及Basic挑战；带浏览器会话的请求按新契约校验。

历史只读恢复中间件继续拒绝所有POST，包括新登录API。不改原恢复脚本或封存文件、不恢复已结束T17D付费环境。复看使用对应冻结前端/原归档或Basic只读HTTP；当前统一登录前端不用于覆盖只读历史恢复站点。恢复实例需要新浏览器登录时，须另建仅允许认证生命周期写入的受审适配，不能移除原业务只读限制。

无SQL迁移。Redis新键使用独立`ac:browser-session:v1:`前缀及TTL，不扫描修改旧模型预算/队列/检查点。回退到`previous-source.zip`中的原前后端及原OpenAPI可恢复原内存Basic行为；回退时禁止新Cookie认证（旧服务不认识新会话），必要时按本部署精确前缀撤销会话，不能清全Redis。旧版本和证据保持，当前源码兼容版本另追加登记，不用新代码冒充旧实验。

安全机制参考：[OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、[OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)。期限、入口绑定和兼容细节是本项目的具体选择。
