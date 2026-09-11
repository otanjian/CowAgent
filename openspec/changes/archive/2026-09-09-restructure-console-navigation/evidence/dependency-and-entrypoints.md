# 跨 change 衔接与依赖证据（phase 0 / 1.4 / 1.5）

复核日期：2026-09-08。本文件记录 design.md 第 8 节相邻 change 的落地核对、身份摘要依赖，以及“跳转后动作 / 业务启动入口 / 草稿适配器 / 账号成员租户写入口”盘点。本 change 只维护自身任务状态，不变更其他 change 的任务勾选。

## 1. 相邻 change 当前核对

| 相邻 change | 本 change 取代/调整 | 继续由原 change 负责 | 当前实际交付（快速核对） |
| --- | --- | --- | --- |
| `prd-02-platform-navigation` | 四分组、旧名称、无反馈占位 | 不重做账号密码/品牌方案 | 侧栏仍为四组（工作台/资源管理/监控/系统设置），非双区域 |
| `add-workbench-appearance-preferences` | 资源分组、顶栏入口 | 个人偏好存储、主题字号、响应式 | `appearance.js`/`appearance.css` 存在；`cow_lang`、`cow_task_notify` 为浏览器本地 |
| `move-session-history-to-workbench` | 名称历史对话 + 稳定地址 | 独立历史页、搜索分组、恢复 | `view-history` 存在；无 hash 地址 |
| `split-agent-configuration-and-workbench` | 管理入口名称/位置 | 使用/管理分离、创建新聊天 | `view-agent-workbench`（使用）与 `view-agents`（管理）并存 |
| `add-sidebar-account-menu`、`extend-sidebar-account-actions` | 顶栏去重、关于/版本、租户控件 | 本人数据、强制改密、退出恢复 | `/auth/me` 返回 `user`/`must_change_password`/`tenants`；账号区有个人资料/修改密码/界面偏好/切换租户/关于/退出 |
| `complete-enterprise-identity-access-control` | 管理页分组/名称；补齐投影 | 身份/授权/事务、安全切片 | `/auth/context` 返回 `effective_permissions`/`is_tenant_admin`/`consumers`；无 `console_pages`。该 change 仍有 8/20 任务未勾选 |
| `add-tenant-identity-access-management` | 新增每成员租户约束 | User/Membership 分离、多租户、最后管理员 | 身份页（tenant/system_user/roles/org）已实现 CRUD |
| `add-workbench-todos`、`add-branding-settings` | 待办筛选呈现、品牌入口位置 | 待办 owner/状态、品牌存储/发布 | `todos.js`、`branding` 视图存在 |

## 2. 身份依赖：/auth/me 与 /auth/context 合同

| 接口 | 返回（当前） | 本 change 需要 | 现状 |
| --- | --- | --- | --- |
| `/auth/me` | `user`(display_name/is_platform_admin)、`must_change_password`、`tenants[]`(code/name/membership) | 有效租户选择、平台资格 | 已实现；无租户时 `tenants:[]` |
| `/auth/context` | `effective_permissions`(sorted)、`is_tenant_admin`、`consumers` | 追加最小 `console_pages` 只读投影 | `consumers` 仅 `web_identity_admin` 可用；缺 `console_pages` |

**结论：** 3.4 需要在 `/auth/context` 追加 `console_pages` 有限投影，同时保持旧字段兼容；3.5 需在零租户时不调用 `/auth/context`。

## 3. “跳转后继续动作”调用盘点（1.5，供 4.2 改写）

| 调用点 | 位置 | 当前行为 | 目标（等待提交/合法同目标后执行一次） |
| --- | --- | --- | --- |
| `openAgentCreateFromComposer` | console.js:4878 | `navigateTo('agents')` 后开表单 | 等待 `committed`/合法同目标，再验资格与上下文后开表单 |
| `openAgentCreateFromModal` | console.js:9166 | `navigateTo('agents')` 后开表单 | 同上 |
| `startSidebarNewChat` | console.js:3302 | `navigateTo('chat')` 后 `newChat()` | `newChat` 受离页确认后再执行；准备值暂存 |
| `startChatWithAgent` | console.js:4555 | 更智能体后建会话 | 等待提交后再建会话，pointer 先暂存 |
| `switchSession` | console.js:10173 | 切换会话 | 等待提交/合法同目标后再切换 |
| 工作文件动作 | dos/drawer | 打开文件面板 | 不创建全局文件页，仅对话面板 |

## 4. 业务启动入口（供 4.7 / 8.8 拆分 initApp）

- `initApp()` 当前在壳就绪后无条件调用，内部创建/恢复会话、`restoreChatState`、`startPolling`、读取知识。需拆为“壳初始化”与“页面/消费者初始化”，管理深链接/无租户/强制改密不触发聊天/知识请求。

## 5. 草稿适配器（供 4.3 未保存保护）

- `wsGuardUnsaved`、`docGuardUnsaved`、`brandingDirty`、`window.__identityAdminDirtyGuard__`、`todos.js` 的 `_dirty`/`closeTodoEdit`。需覆盖同页页签/对象切换及知识导入。

## 6. 账号/成员/租户/初始化写入口（供 3.7/3.8 成员租户约束）

- 身份服务写入口：创建成员、绑定已存在账号、停用/解绑 Membership、停用租户、恢复账号、初始化/迁移。需统一加入“启用账号至少一条 active Membership 关联 active Tenant”事务校验。当前 `_ensureTenantSelected` 对无 Membership 平台管理员返回 `platform` —— 本 change 收紧为受限恢复。
