# 导航基线快照（脱敏）

复核日期：2026-09-08。本文件记录实施前 `channel/web` 控制台的导航基线，用于 phase 0（1.1/1.2/1.3）与后续迁移表核对。所有值均为结构快照，不代表功能验收结论。

## 1. 侧栏条目与页面容器对照

侧栏 `#sidebar nav` 当前共有 **22 个 `data-view` 入口**，对应 **17 个真实页面容器**（`id="view-<id>"`）与 **5 个无反馈占位入口**。

| # | data-view | 菜单文案 (i18n) | 分组 | 页面容器 | 点击行为 |
| --- | --- | --- | --- | --- | --- |
| 1 | `chat` | 对话 (`menu_chat`) | 工作台 | `view-chat` | 真实视图 |
| 2 | `history` | 历史会话 (`session_history`) | 工作台 | `view-history` | 真实视图 |
| 3 | `agent-workbench` | 智能体 (`menu_agents`) | 工作台 | `view-agent-workbench` | 真实视图 |
| 4 | `scenarios` | 场景应用 (`menu_scenarios`) | 工作台 | 无 | **无反馈占位** |
| 5 | `todo` | 待办 (`menu_todo`) | 工作台 | `view-todo` | 真实视图 |
| 6 | `tasks` | 定时 (`menu_tasks`) | 工作台 | `view-tasks` | 真实视图 |
| 7 | `config` | 配置 (`menu_config`) | 资源管理(details) | `view-config` | 真实视图 |
| 8 | `agents` | 智能体配置 (`menu_agent_config`) | 资源管理(details) | `view-agents` | 真实视图 |
| 9 | `skills` | 技能 (`menu_skills`) | 资源管理(details) | `view-skills` | 真实视图 |
| 10 | `memory` | 记忆 (`menu_memory`) | 资源管理(details) | `view-memory` | 真实视图 |
| 11 | `knowledge` | 知识 (`menu_knowledge`) | 资源管理(details) | `view-knowledge` | 真实视图 |
| 12 | `channels` | 通道 (`menu_channels`) | 资源管理(details) | `view-channels` | 真实视图 |
| 13 | `logs` | 日志 (`menu_logs`) | 监控 | `view-logs` | 真实视图 |
| 14 | `platform` | 平台设置 (`menu_platform`) | 系统设置 | 无 | **无反馈占位** |
| 15 | `tenant` | 租户 (`menu_tenant`) | 系统设置 | `view-tenant` | 真实视图 |
| 16 | `system_user` | 用户 (`menu_system_user`) | 系统设置 | `view-system_user` | 真实视图 |
| 17 | `roles` | 角色权限 (`menu_roles`) | 系统设置 | `view-roles` | 真实视图 |
| 18 | `org` | 组织架构 (`menu_org`) | 系统设置 | `view-org` | 真实视图 |
| 19 | `branding` | 品牌设置 (`menu_branding`) | 系统设置 | `view-branding` | 真实视图 |
| 20 | `audit` | 审计 (`menu_audit`) | 系统设置 | 无 | **无反馈占位** |
| 21 | `backup` | 备份升级 (`menu_backup`) | 系统设置 | 无 | **无反馈占位** |
| 22 | `open_api` | 开放 API (`menu_open_api`) | 系统设置 | 无 | **无反馈占位** |

**结论：** 5 个无反馈占位为 `scenarios`、`platform`、`audit`、`backup`、`open_api`。其中 `scenarios` 在 `VIEW_META` 中有登记（但无容器，点击不切换视图）；`platform`、`audit`、`backup`、`open_api` 均**不在** `VIEW_META` 中，`navigateTo(viewId)` 会提前 `return`，点击完全无动作。

## 2. `VIEW_META` 登记（console.js:3157）

```js
const VIEW_META = {
    chat:     { group: 'nav_chat',    page: 'menu_chat' },
    history:  { group: 'nav_chat',    page: 'session_history' },
    'agent-workbench': { group: 'nav_chat', page: 'menu_agents' },
    todo:     { group: 'nav_chat',    page: 'menu_todo' },
    tasks:    { group: 'nav_chat',    page: 'menu_tasks' },
    scenarios:{ group: 'nav_chat',    page: 'menu_scenarios' },
    config:   { group: 'nav_manage',  page: 'menu_config' },
    agents:   { group: 'nav_manage',  page: 'menu_agent_config' },
    skills:   { group: 'nav_manage',  page: 'menu_skills' },
    memory:   { group: 'nav_manage',  page: 'menu_memory' },
    knowledge:{ group: 'nav_manage',  page: 'menu_knowledge' },
    channels: { group: 'nav_manage',  page: 'menu_channels' },
    logs:     { group: 'nav_monitor', page: 'menu_logs' },
    branding: { group: 'nav_system',  page: 'menu_branding' },
    tenant:      { group: 'nav_system', page: 'menu_tenant' },
    system_user: { group: 'nav_system', page: 'menu_system_user' },
    roles:       { group: 'nav_system', page: 'menu_roles' },
    org:         { group: 'nav_system', page: 'menu_org' },
};
```

- 分组 `group` 引用类型：`nav_chat` / `nav_manage` / `nav_monitor` / `nav_system`。其中 `nav_manage` 在 HTML 中实际是 `<details id="sidebar-resources">`（资源管理），`nav_monitor` 与 `nav_system` 为 `.menu-group`。
- 页面 `page` 为面包屑页名 i18n key。

## 3. 页面载入函数与生命周期

`navigateTo(viewId)`（console.js:3181）当前行为：

1. 校验 `VIEW_META[viewId]`，未登记即 `return`（占位项在此被吞掉）。
2. `branding` 未保存草稿保护（`brandingDirty`）。
3. `tenant/system_user/roles/org` 离页保护（`window.__identityAdminDirtyGuard__`）。
4. 非当前视图时 `agentNavigationVersion++`，`cancelAgentStart()`。
5. 离开 `history` 标记 dirty 并取消请求、关闭会话动作菜单。
6. 切换 `.view` 与 `.sidebar-item` 激活态，展开所属分组。
7. 更新面包屑 `#breadcrumb-group` / `#breadcrumb-page`。
8. 按 viewId 调用页面初始化：`initBrandingView / loadTenantView / loadMembersView / loadRolesView / loadOrgView / loadAgentCatalog / loadAgentWorkbench / loadSessionList` 等。
9. 内联 `navigateTo` 二次覆盖（console.js:15675）有 `docGuardUnsaved` 包装。

## 4. 无页面级 hash 地址

- 当前控制台**没有**页面级 hash 路由；`navigateTo` 仅做 DOM 切换。
- 地址栏始终为 `/chat`，无法复制/刷新对应页面、无法前进后退到页面。
- `/_` 启动逻辑 `initApp` 无条件创建/恢复会话、启动轮询、读取知识（不区分目标页面），见设计 review P1。

## 5. 无反馈占位删除后的受保护业务入口

- `identity-admin.js` 已交付全局账号页签与身份审计子视图（宿主为 `tenant`/`system_user`/`roles`/`org` 视图内部）。这些**不是**侧栏 `audit` 空链接，移除侧栏 `audit` 不得删除其真实功能。

## 6. 依赖 identity 服务现状

- `auth/service.py` 的 `/auth/context`（`context_for_tenant`）返回 `effective_permissions` / `is_tenant_admin` / `consumers`。
- `_consumer_availability()` 目前仅 `web_identity_admin` 为 `available=True`；`chat/tools/files/scheduler/openai_api/desktop_enterprise/mcp/channels` 均为 `available:False, reason:"deferred"`。
- `/auth/context` 当前**没有** `console_pages` 最小投影字段（本 change 3.4 需追加）。
- `/auth/me`（`self_context`）返回 `user` / `must_change_password` / `tenants`；账号无有效租户时 `tenants` 为空列表。

## 7. `_ensureTenantSelected` 与零租户平台分支

- console.js 中 `_enterAccountApp` 调 `_ensureTenantSelected()`；`ready === 'platform'` 时 `navigateTo('tenant')`，即**现允许平台管理员无 Membership 直达平台管理**（design review 指出该行为将被本 change 收紧为受限恢复）。
