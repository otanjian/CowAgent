# 方案审查与优化记录

审查日期：2026-09-08。范围为提案、设计、规范、任务与现有 Web 代码的静态对照；本轮仅更新 change 文档，未实施或运行产品认证操作。

| 发现 | 现有代码依据 | 已纳入的修正 |
| --- | --- | --- |
| 初始检查失败仍初始化业务，可能被业务 401 带入错误的共享密码表单 | `console.js` 初始化 `/auth/check` 的 catch 调用 `initApp()`，身份模式默认 legacy | unknown 使用现有登录遮罩显示中性加载/错误与重试；认证结果确认后才进入正确入口，首次成功只初始化一次 |
| 单一请求代次仅保护文字写入，不能防止旧 401 清除新身份 | `console.js` 全局 401 包装直接调用 `showLoginScreen()` | 身份代次与检查序号分离，覆盖完整账号回调及 401 失效副作用；保留当前身份真实 401 和调用方原响应 |
| 多租户进入按钮的 onclick 可能残留，拦截下一次登录 | `_showTenantPicker()` 安装按钮 onclick，`showLoginScreen()` 只重设表单 onsubmit | 选择完成、失效及重新显示登录表单时清理旧回调与临时选项，保留原选择界面 |
| 已登录但缺少资料被方案当成未知身份，失去底部退出入口 | `/auth/check` 的 authenticated 与 user 是独立信息；原规范混为同一失败态 | 分离认证结论与资料完整性，已认证但资料缺失仍可退出和重试 |
| 退出非成功 JSON 没有恢复路径 | `handleLogout()` 只处理 success 和 Promise rejection | HTTP/业务错误、解析和网络失败统一为可恢复的退出未确认，两个入口防重复提交 |
| 账号重试若复用整个启动链，会重复请求并抢聊天焦点 | `initApp()` 刷新工作区、会话、知识、版本并调用 chatInput.focus | 启动重试与控制台内重试分开，后者只刷新账号并保留当前业务状态和焦点 |
| 顶部菜单阻止 click 冒泡，账号菜单可能无法外部关闭 | `toggleLangMenu()`、`toggleTenantMenu()` 调用 stopPropagation | 双向菜单互斥，外部检测使用捕获 pointerdown 或等价联动；补关闭态 Tab、焦点离开和异步文本更新行为 |
| 复用租户请求封装可能误判全局登录失效 | `DbAuthCheckHandler` 解析租户头，`resolve_context` 的无租户分支才检查全局身份 | 本人检查不附加租户头，明确 legacy 响应的缺省字段规则 |

实施范围保持三个 Web 前端接入文件及必要测试/使用说明。顶部租户自动选择时序、管理权限接口、后端认证/隔离与跨标签页协调均不在本次重构范围。

验证计划保持四阶段、17 项未勾选任务，采用现有测试工具复现状态与并发，实际接口验收确认契约，代表性视口检查布局；不做语言、主题、视口和租户数量的全组合测试。

本轮 `openspec validate add-sidebar-account-menu --type change --strict --no-interactive` 已通过；`openspec status --change add-sidebar-account-menu` 确认规划产物 4/4 完成。全部 17 项实施任务仍未勾选，文档就绪不等于产品实现或验收完成。
