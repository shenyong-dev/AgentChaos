# P04真人单题诊断入口

2026-11-15新版状态：已完成[已知问题修复](P04-USABILITY-FIX.md)。为保持旧封存内容，18054改为原HTML字节的静态只读服务（写405）；原动态记录器已停止。下文启动/恢复命令为历史记录，不能用于修改后的源码继续旧轮。新18055仅机械界面预览，不是新真人入口，也不重置原封存或账本。

2026-11-15后续状态：P01已由本人封存，5事件/1题/1问及原回执核对通过，自报“不确定”。已另存[只读定性复核](../evidence/P04/diagnostic-review.md)，发现助手分析与记录器导航的实用性缺口，未给原答案或模型计算正确率；当前入口仅保留封存阅读，不自动开其他人员。下文保留启动时点记录。

2026-11-15。按用户“启动真人使用”“额度不影响，随便用”开启新单题诊断；[模式与范围](../fixtures/P04/pilot-20261115.md)明确参考待人工核对、不计算正确率。原准备稿不回填通过，旧T19/T19B不恢复。

## 已启动的入口

本机loopback18054，完整私有URL已提供用户，另存`.local/p04-pilot-20261115/entry-url.txt`。只建立本轮P01槽位/U01，没有代为开始、提问、作答或封存；欢迎页GET200。本人需选择熟悉程度与项目参与情况、勾选亲自操作后开始。

试用为独立记录页，真实读取当前approved dev修订：用例7/hash`78c1eae0a6aaddbf761dadbf7e5467928c090ac9f934e4de2df79409582be0bf`，9/hash`b67632305149853fdbca83e42d9bb83151daa8ea720dae3ffeeb121ddc7570ec`，10/hash`4f17658b8d397dee0545168a5723cbb99d3b4b66395534dbc2c3daa64b09c4f9`，均修订1。不借用当前未启动的API8000/Vue5173，也不把此记录器当完整平台真人验收。

新代码仅在`backend/benchmarks/p04_trial`及专项测试；重用当前CopilotService、Prompt和SourceTools，内存Settings副本及case.read导航白名单，不改生产app源码或日常开关。草稿随提问/回执追加保存，封存与退出分开，原问题先fsync，未知不自动重发，回执SHA与原事件绑定。

## 工程检查与修复

- 最终12项后端专项通过（23.13秒）：真实HTTP/Origin/Host/CSRF、显式一次发送、未知只查回执、回执与孤立文件完整性、字段错误/草稿/退出/封存、隔离真实Redis计量/失账本拒绝重发额度，以及当前真实开发用例＋替身网关的来源/友好按钮/过期回执不重建。
- 3项真实浏览器机械流程通过（3.0秒）：1440浅色、390深色、320减少动态，原文折叠后引用导航、空白答案、提问保留草稿、校验回填、封存或退出，以及无横向溢出和页面脚本错误。截图已目视检查。
- Ruff通过。没有改Vue生产代码，不重复前端生产构建或无关全套测试。
- 同当前模型一次独立review发现1P1和2P2：no-referrer导致POST的Origin=null而被拒绝；独立form丢失未提交草稿；推测和事实混排。均已修为same-origin、共享form＋追加draft/恢复、必要语义组名。同一审查者核对修复，无新确定P1/P2；不是独立人工判据审核。

首轮浏览器三项失败及trace均保留。首个HTTP控制因测试客户端代理环境得到502，改为直连本机后通过；它与实际浏览器Origin问题分别记录。初21项lint及后续未用测试导入已修，原日志保留。所有机械填写位于`ui-controls`或临时测试目录，不是真人证据。

## 真实供应商与预算

仅启动前做一次独立连接检查，不涉及任何试用题答案：正式接口返回JSON connection=ok，1请求，输入50/输出5token。单列`connectivity-calls`，不当参与者提问或助手质量证据。SDK提示response_format转入model_kwargs，回执正常，未重发。root启动时1已完成、held0、unknown0，参考计费估算21微美元，非供应商实际账单；用户累计金额/请求不限的授权仍有效。

独立授权`P04-20261115-copilot-open-1`，新root limit=-1；approval/initialization先落盘且create-only，绑定Redis实例及nonce。来源漂移、未知用量、协议超限或失账本停止新调用，不重置旧实验或自动重建。其他角色没有新增执行授权。

## 保全与恢复

准备时配置的本机Redis16380未运行，先将原配置、持久AOF数据及日志等8文件共561,794,728字节复制核对到私有`redis-before`，再用原`scripts/start-redis.ps1`恢复。等待PONG后读取21份原root为基线；新root初始化及连接检查后原21份逐字段保持。没有清库或初始化旧预算；Redis的此次恢复和新实例run_id单列，不冒称全程未管理Redis。原旧试用的身份绑定仍不恢复。

旧参与者、`.env`和原P04准备材料共12份SHA在启动核对时保持；原160后端应用源及旧证据继续另存，不改原Prompt、用例、主业务记录或版本。未启动/停止API、Vue、Worker、Dispatcher，未访问云端/SSH、Git或赛事包；原18052/18053预览不动。模型连接检查另更新既有模型连通缓存，不等于业务执行。

当前独占服务器exec session为13368，仅供本轮现场续作定位；后续须以实时18054监听/CIM命令核对，不能按旧PID管理。若同一来源、Redis实例、原账本及期限均未变，可用原包/原入口恢复，不改变token或期限：

```powershell
Set-Location D:\AgentChaos
$env:PYTHONPATH='D:\AgentChaos\backend'
$env:PYTHONIOENCODING='utf-8'
& backend/.venv/Scripts/python.exe -m benchmarks.p04_trial.collector `
  --package .local/p04-pilot-20261115/packages/45719463e1576dcab74a6a7dea26098117749f42ae2480d4d9a23d91584c7086 `
  --participant P01 --port 18054
```

退出状态、已封存答案、原调用和未知预留不能重置。源或身份变化时停查，不删除entry/approval/initialization重新发额度。入口不承诺关闭宿主或电脑重启后的独立保活。

启动核对点为0个人事件，随后已将欢迎页交给用户，后续人数/调用以真实记录为准；未完成或未封存时不读答案调题。收到本人封存/退出后再另存复核，原记录不改写。P04实际效果、独立语义校准及P02新实验仍未完成，T22/T23团队接管、T24未触发。
