# T15 整体视觉与交互反馈（最新）

2026-10-29按用户视觉文档完成全局蓝灰主题、13处统一选择器、概览层次与键盘快速跳转。具体颜色、尺寸、影响和参考见[前端体验说明](frontend-experience.md)，实测8项专项及5+2既有回归通过。下文保留先前设计过程，当前以本段与新体验说明为准；没有T16、付费调用或Git发布。

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


## T14整合（2026-10-29）

审计发现旧页面依赖手填UUID、缺少配置/运行入口、凭据区占高，手机故障按钮与说明重叠。保留深绿品牌和Element Plus，新增概览—对象—开发库—工作台导航，采用原生Grid和真实服务端分页；加载后不重复铺开工作区凭据。桌面三栏、窄屏标签页、缩小重复状态区，保留明确状态文字和减少动态效果。没有为展示混装其他设计系统。

确认流程使用已有[Element Plus Dialog](https://element-plus.org/en-US/component/dialog.html)的标题、正文、footer与焦点交互；另实际用键盘测试弹窗焦点，不由文档推断“已无障碍”。新事件不自动滚动、左右事实与预期分开、源码兼容不冒充模型验证。最新截图/录屏与审计见acceptance/T14.md及evidence/T14/；之前T08引用保持原记录。

### T14侧栏重设计，2026-10-29团队外观反馈

用户要求现代、克制的工具界面。对照[shadcn/ui Sidebar](https://ui.shadcn.com/docs/components/base/sidebar)的分组/折叠/移动端结构、[Langfuse AppSidebar](https://github.com/langfuse/langfuse/blob/main/web/src/components/nav/AppSidebar/AppSidebar.tsx)的项目上下文和分组组织、[Twenty NavigationDrawerItem](https://github.com/twentyhq/twenty/blob/main/packages/twenty-front/src/modules/ui/navigation/navigation-drawer/components/NavigationDrawerItem.tsx)的图标/选中/展开状态。这里只借鉴组织与交互方式，没有移植其React组件或混装框架。

侧栏改为浅灰中性表面、细分隔线、同系列SVG图标和单一浅绿选中态；使用紧凑AC字标，移除旧侧栏的说明长文。导航按工作区、质量与审阅、管理分组，保持7个入口和原URL。抽出AppSidebar与navigation.css，桌面244px/折叠76px，760px以下使用现有Element Plus Drawer；可滚动导航和固定品牌/页脚保证短屏可达。详情路由映射到所属菜单，并提供跳过导航、折叠提示和抽屉焦点约束。

复用已安装的`@element-plus/icons-vue@2.3.2`，补充直接依赖声明；lock只增加根依赖一行，没有引入新包版本或安装新程序。折叠仅保留当前页面内存，不存储凭据，不伪造项目切换/健康/运行计数。参考源码和组件文档仅说明设计依据，实际检查见T14验收追加节。
# T15 报告工作区追加

2026-10-29在T14导航基础上追加“缺陷与报告”，质量与审阅组现为3入口，总入口8项。沿用现有色彩、留白、SVG和响应式抽屉，不新增组件依赖。

报告页按运行报告/缺陷复核/版本对比分视图：先展示冻结运行和真实分母，再给原执行/SQL链接；原始配置与JSON折叠。搜索选择器使用Element Plus，长UUID分行、中文状态、手机限宽。导出显示实际文件大小、SHA和有效期，重试保留原请求标识。缺陷弹窗将预期/实际并列，复核历史与原始断言折叠，先看证据、填写依据、核对内容，再显式保存；不预填人工同意声明。对比列出每个条件和配对执行，条件差异时隐藏提升幅度。

桌面1440与手机390实际下载截图、复核弹窗、对比及视频在docs/evidence/T15/offline-807d918abcfc/；5项UI加1项预览返回专项通过，原失败与复检日志保留。核对阶段隐藏输入，返回重开需重新填写说明/声明，不自动保存。沿用T14完整Dialog/Overlay样式，长内容内部滚动。免费控制夹具不作为模型质量或团队人工标签。
