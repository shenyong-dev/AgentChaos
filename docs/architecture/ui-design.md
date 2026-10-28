# T08 工作台界面调整

2026-10-27—28，Asia/Shanghai。用户要求参考GitHub改善现有Agent前端。本次范围是工程环境、运行证据和T08判定展示，保留既有路由与主导航、Vue3/Element Plus、深绿品牌、内存凭据及只读证据流；不提前实现T14完整工作台。

## 参考与取舍

| 已读取的官方仓库源码 | 采用的思路 | 本次边界 |
|---|---|---|
| [Dify tracing-panel](https://github.com/langgenius/dify/blob/42bde2e983e8e2e2e3b6094d08fb68b1cfb1be19/web/app/components/workflow/run/tracing-panel.tsx) | 将轨迹导航与被选节点的过程/结果详情分开，按选择聚焦阅读 | 用已有真实事件和断言引用，不添加未实现的流程画布/Agent编排器 |
| [Langfuse 桌面轨迹导航布局](https://github.com/langfuse/langfuse/blob/d5f366887bacce3472116569899369444c5201ea/web/src/features/traces/components/TracePanelNavigationLayoutDesktop/TracePanelNavigationLayoutDesktop.tsx) | 导航/内容分区与可收起结构，给细节留出空间 | 新增判定列表/预期实际双栏及可折叠原始记录，不接外部观测服务 |

本次源码抓取与固定commit内容逐字节一致，SHA256见[参考登记](../evidence/T08/ui-references.json)。只借鉴交互组织，未复制第三方组件代码，未引入React或混装设计系统。

## 现状审计与实际改动

旧界面深绿侧栏和真实状态值得保留；小字偏多、输入区长驻占高、原始JSON直接铺开，阅读焦点不明显。当前以单一绿色强调色、冷中性底色、统一间距/圆角和系统中文无衬线字体改善可读性，无新增字体/图标下载。

“调用轨迹/判定结果”分别服务定位过程和复核结论；载入后凭据区收起，规则结果展示独立任务达成状态、预期/实际/原因/证据。原始JSON和版本指纹可展开。引用可定位持久事件，不要求事件已在当前500条窗口，也不改变SSE游标。

手机改为原生断言选择器再读详情，避免窄屏同时挤两栏；桌面保留左侧列表/右侧明细。保留键盘焦点、状态文字、空/加载/错误态及减少动态效果设置，不用装饰动画模拟执行。

Vite支持AC_API_PROXY_TARGET作为本轮隔离实验入口，原API_PROXY_TARGET继续兼容，主环境默认8000；实验UI5175代理独立API18008。不会通过切换代理把旧UUID伪装成主库运行。

11项T08页面检查涵盖读取实际存储结果、预期/实际差异、按引用定位、幂等重评、390px手机选择器、无横向溢出/页面错误/凭据持久存储；原环境、解析器、空页和当前认证回放另6项通过。原始截图见[当前离线验收](../evidence/T08/offline-b2d327780c50/)；“离线模型复验”始终明确可见。
