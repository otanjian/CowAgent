# 阶段 A：菜单导航与依赖基线证据

## 1. 当前 Web 菜单结构（实施前基线）

来源：`channel/web/chat.html` 侧边栏（`<nav>`）+ `channel/web/static/js/console.js` 的 `VIEW_META`。

| 分组 | data-group | 菜单项 | data-view | 当前分组 |
| --- | --- | --- | --- | --- |
| 工作台 | `chat` | 对话 | `chat` | 工作台 |
| 工作台 | `chat` | 智能体 | `agents` | 工作台 |
| 工作台 | `chat` | 待办 | `todo` | 工作台（占位） |
| 工作台 | `chat` | 定时 | `tasks` | 工作台 |
| 工作台 | `chat` | 场景应用 | `scenarios` | 工作台（占位） |
| 管理 | `manage` | 配置 | `config` | 管理 |
| 管理 | `manage` | 技能 | `skills` | 管理 |
| 管理 | `manage` | 记忆 | `memory` | 管理 |
| 管理 | `manage` | 知识 | `knowledge` | 管理 |
| 管理 | `manage` | 通道 | `channels` | 管理 |
| 监控 | `monitor` | 日志 | `logs` | 监控 |
| 系统设置 | `system` | 平台设置等九项 | `platform` 等 | 系统设置 |

`VIEW_META` 中 `agents` 归属 `group: 'nav_chat'`（工作台），`page: 'menu_agents'`。

## 2. 本 change 与相邻 change 的差异

### prd-02-platform-navigation（已归档规划，未实现）
- 其 `design.md` 第 29 行明确「管理分组菜单归属……（不含智能体与定时）」——即 `prd-02` 草案要求**管理分组不含智能体**。
- 前端已实现该结构（管理分组当前无智能体项，智能体在工作台），见上文基线。

### add-workbench-todos（规划中）
- 待办 `todo`、定时 `tasks` 均位于工作台分组，`prd-02` 将「定时」搬移至「工作台」并置于「待办」之后，当前实际即如此。
- 本 change 不改动待办/定时菜单位置，仅在工作台新增 `agent-workbench` 使用页入口。

### 本 change 的增量
- 将智能体**配置**入口从工作台移至「管理 → 配置」之后，更名「智能体配置」。
- 工作台原智能体位置保留「智能体」菜单，改为 `agent-workbench` 使用页。
- 其余菜单归属与顺序保持不变。

## 3. 智能体配置页与聊天入口现状（实施前基线）

- 智能体配置页：`data-view="agents"`（容器 `id="view-agents"`，`class="view agents-view"`），含 `agents-grid` 与详情抽屉 `agent-detail`（配置编辑器）。
- 工作台调用：`navigateTo('agents')` 时调用 `loadAgentCatalog()`，该函数会刷新管理数据、选择器、活动智能体与 `activeAgentId` 回退——**有副作用**，不能直接作为无副作用的工作台启动校验。
- 详情页聊天入口：`renderAgentDetail()` 内 `startChatWithAgent('${agent.id}')`。

## 4. 已完成/未完成的相邻改动标记

- `prd-02-platform-navigation`：`tasks.md` 中 1.1～4.4 已勾选（前端四分组重构、占位项、系统设置）；5.1～6.5 未勾选（登录账号体系、验证回归）。**导航骨架已落地**。
- `add-workbench-todos`：规划 artifact 完备，任务未执行。
- `add-tenant-identity-access-management`：29 项任务全部未执行（进度 0/29）。

## 5. 身份模式判定结论

- `web_channel.py` 的 Web 服务路由仅使用 `_require_auth()`（`cow_auth_token` cookie / Bearer / query token），**未接入** `auth/policy.py` 的 `agent.read`、租户、成员资格或 RBAC。
- `auth/policy.py` 定义了权限目录（`agent.read` 等）与内置角色，但 Web 控制台未调用 `has_permission`/授权解析。
- 因此当前实际交付模式为 **legacy**（单实例、纯密码登录、无租户/成员隔离）。`add-tenant-identity-access-management` 的 database 授权与运行隔离**未落地**，本 change 不假定其存在，也不扩大实现身份体系。
- 结论：工作台读取投影沿用 legacy 的 `_require_auth()` 策略；`can_chat` 在 legacy 下恒为可启动（除非目标停用/删除），无需 `runtime_not_enabled` 门控。
