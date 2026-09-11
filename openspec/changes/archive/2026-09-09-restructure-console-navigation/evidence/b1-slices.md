# B1 Evidence — Navigation switch & member→tenant continuity

> 记录本 change 在阶段 B1 已交付且经测试验证的后端切片。所有结论只针对本轮明确需求，
> 不把未开放消费者标记为已开放，不为排版完整性开放功能。

## 1. 呈现开关 `web_navigation_mode`（任务 3.2）

**合同（只读、布局级）**

- 合法值：`classic` | `split`；缺省/invalid 一律回退 `classic`。
- 服务端在页面响应中注入经白名单校验的值，前端不信任任意字符串。
- 该开关**不改变**身份模式（legacy/database）、认证/授权、任一消费者开闭状态。

**实现**

- `config.py`：新增默认 `"web_navigation_mode": "classic"`（与 `identity_mode` 同处默认段）。
- `channel/web/web_channel.py`：
  - 新增 `_NAVIGATION_MODES = ("classic", "split")` 与 `_web_navigation_mode()`，非法值安全回退。
  - `ChatHandler.GET` 注入 `{{COW_NAVIGATION_MODE}}`；`WebChannel.chat_page()` 同样注入（兼容未用路径）。
- `channel/web/chat.html`：新增 `window.__COW_NAVIGATION_MODE__ = '{{COW_NAVIGATION_MODE}}'`。
- `channel/web/static/js/console.js`：新增 `_NAVIGATION_MODES` 与 `_navigationMode()`（回退 `classic`）；成功进入时在 `#app` 上写入 `data-nav-mode` 作为布局钩子，**不影响** classic 默认行为。

**验证**

- `tests/test_web_navigation_mode.py`：合法值、大小写/空白规整、非法值回退、缺省回退、`ChatHandler` 注入与回退。
- `tests/test_web_chat_content_type.py`：注入未破坏 Content-Type。
- `node --check console.js`：语法通过。

## 2. 成员→租户连续性（任务 3.7–3.9）

**约束（2026-09-08 用户追加）**

- database 模式每个 Membership 必须关联真实 Tenant；每个启用 User（含平台管理员）
  至少有一条 active Membership 关联 active Tenant，允许多租户。
- 停用最后一条有效关系、停用租户、恢复账号等会减少有效归属的写入口，须在
  **同一写事务内**重验归属计数、操作者资格与版本；任一启用账号归属为零时拒绝整次事务
  （409，业务原因 `last_active_tenant_required`）。既有的最后平台/租户管理员规则同时成立。

**实现（`auth/service.py`）**

- `_SIGNED_CONSOLE_PAGES` + `_console_pages_projection()`：`/auth/context` 只读“有限页面/页签”
  投影（`available/read_allowed/scope/reason/actions`），由既有授权与消费者状态派生，非授权。
- `_user_has_active_tenant(con, user_id)`：该用户是否存在一条 active Membership 关联 active Tenant。
- `_require_active_tenant_for_enabled_user(con, user_id)`：启用账号无有效租户即拒绝。
- `_check_all_affected_active_tenant_continuity(con, user_ids)`：批量重验受影响启用账号。
- 集成点：
  - `set_platform_user_status`：启用账号前要求已存在有效租户（`update_member` 路径同理）。
  - `update_member`：停用成员前重验该成员账号有效租户。
  - `set_tenant_status`：停用租户前对受影响启用成员逐一重验（排除刚停用租户自身）。

**验证**

- `tests/test_platform_user_admin.py`：新增
  `test_reenable_requires_an_active_tenant`（无租户启用被拒绝）、
  `test_reenable_ok_with_an_active_tenant`（有租户可启用）。
- `tests/test_revocation_takes_effect.py`：为单租户场景补充第二有效租户，使
  成员停用/租户停用写合法（不改变“撤权立即生效”的断言），新增 `_second_tenant` 工具函数。
- 全量身份/租户测试（21 个文件，共 248 项）通过。

## 边界与未覆盖

- ~~前端“无租户平台直达分支”替换（任务 3.9 后半）属于导航深链接/受限恢复，需浏览器验收，
  留待 B2/B3 一并处理，不在此切片断言。~~ **已在本轮补齐前端**，见下节。
- 任务 3.10 的只读归属预检与显式补分配工具为独立维护工具，单列。

## 3. 目标导航结构与权限可用性（2026-09-08 用户追加“按目标结构实施”）

### 目标结构（侧栏）

```
工作台
  ＋ 新建对话
  对话           data-view=chat
  历史对话       data-view=history
  智能体         data-view=agent-workbench
  我的待办       data-view=todo
  定时任务       data-view=tasks
  知识库         data-view=knowledge

管理控制台〔按权限展示〕
  智能体开发
    智能体管理   data-view=agents
    工具与技能   data-view=skills
    记忆管理     data-view=memory
  模型与接入
    模型服务     data-view=config
    消息渠道     data-view=channels
  组织与权限〔当前租户〕
    成员管理     data-view=system_user
    角色权限     data-view=roles
    组织架构     data-view=org
  平台运维〔平台范围〕
    租户管理     data-view=tenant
    系统设置     data-view=platform
    品牌设置     data-view=branding
    运行日志     data-view=logs

账号菜单  个人资料 / 账号安全 / 个人偏好 / 帮助与关于 / 退出登录
```

### 实现（`channel/web/chat.html` + `channel/web/static/js/console.js`）

- **侧栏重组**：由旧“管理与监控 + 资源管理折叠组 + 监控组 + 系统设置”改为
  “工作台 + 管理控制台（四分组）”。保留全部既有 `data-view`、`.sidebar-item`、
  `.menu-group` 类名与 `#sidebar-nav` 容器，`navigateTo`/面包屑/测试选择器继续工作。
- **知识库移入工作台**，消息渠道/模型服务归入“模型与接入”，成员/角色/组织归入
  “组织与权限〔当前租户〕”，租户/系统设置/品牌/日志归入“平台运维〔平台范围〕”。
- **分组范围标记**：`组织与权限` 与 `平台运维` 分组名保留旧范围语义（租户/平台）仅在 `VIEW_META`/投影中体现，侧栏不再显示「当前租户」「平台范围」徽标（用户要求去除，避免视觉干扰）。
- **VIEW_META 收口**：按新分组映射每个 view 的 `group`/`page`（工作台、智能体开发、
  模型与接入、组织与权限、平台运维），移除已无入口的 `scenarios`/`audit` 键。
- **i18n**：三语（zh/zh-Hant/en）同步新增 `nav_workbench`、`nav_admin_console`、
  `nav_group_agent_dev`、`nav_group_model_access`、`nav_group_org_perm`、
  `nav_group_platform_ops`（范围徽标键 `nav_scope_*` 按用户要求移除）；
  并改名 `menu_config`→模型服务、`menu_skills`→工具与技能、`menu_memory`→记忆管理、
  `menu_platform`→系统设置、`menu_agents`(工作台)保留。
- **账号菜单合并**：移除“切换租户”（由顶栏 `#tenant-selector` 承担），
  改名“修改密码”→账号安全、“界面偏好”→个人偏好、“关于当前品牌”→帮助与关于；
  保留“个人资料/退出登录”。

### 权限可用性（任务 3.5 前端）

- `_applySidebarPermissions()` 重写：database 模式下，非平台管理员且非当前租户
  tenant_admin 时隐藏整块“管理控制台”（`sidebar-hidden-admin-area`）与平台范围分组
  （`sidebar-hidden-platform-scope`）；平台范围组仅平台管理员可见；legacy 模式保持全量导航。
  仅为展示投影，逐页仍由服务端独立授权（`console_pages` 权威判定）。

### 无租户平台直达分支替换（任务 3.9 前端）

- `_enterAccountApp()`：当 `ready === 'platform'`（平台管理员但无有效租户归属）时
  **不再** `navigateTo('tenant')`，改为停留在受限恢复门（显示“待分配说明 + 重试”，
  保持 `login-overlay` 可见、`app` 隐藏、不需租户初始化），符合
  `member-tenant-assignment` 规范“无租户仅开放受限恢复”。
- 新增 i18n `account_assign_pending`（三语）。

### 验证

- `tests/test_sidebar_account_frontend.cjs`：改写“平台管理员无归属”用例，
  断言进入受限恢复门（app 隐藏、login-overlay 可见、`currentView !== 'tenant'`、重试可见）。
- 全量依赖无关前端测试通过（sidebar/agent/appearance/session-history/todo/identity-admin，
  仅余 1 个既有 `welcomeHeroDescription` harness 引用错误）。
- 说明：本切片为前端结构+可用性展示；完整 hash 路由（任务 4.x）、浏览器验收
  （2.5/8.5）与真实消费者开放仍保留在后续阶段。
