# Stage 8 evidence — 个人控制台与工具技能目录（任务 8.1–8.6）

本文件记录 Stage 8 的实际落地与验证证据。所有命令均在仓库根目录执行；
「通过」均指本机实际跑出的结果，未跑过的项在文末「尚未通过/待验收」列出。

## 0. 落地范围

| 层 | 文件 | 内容 |
| --- | --- | --- |
| 权限投影 | `auth/service.py` `_console_pages_projection` / `_personal_page_states` / `_personal_channel_projection` / `personal_channel_types()` | 五类个人页面的 read / config / execution 三态、有限动词、个人渠道实例动作 |
| 资源目录 | `auth/service.py` `list_personal_resource_configs` / `save_personal_resource_config` / `clear_personal_resource_config` | 只含本人当前授权 ∩ 已保存；保存只写本人行，清理连带撤销本人凭证 |
| 个人记忆 | `agent/memory/personal.py` `_entry_info` | 行级 `actions`（edit/delete），与私有 Agent 记忆分开入口与存储域 |
| Web 入口 | `channel/web/web_channel.py` `AgentsHandler`(`view=personal`)、`PersonalResourceHandler`、`PersonalMemoryHandler`、`PersonalChannelHandler` | 会话固定 tenant/owner 的自助面 |
| 路由 | `channel/web/route_registry.py` | `/api/personal/resources` 等 `personal` 策略条目（`fork:member-personal-console`） |
| 前端 | `channel/web/static/js/personal-console.js`、`i18n/personal-console.js`、`console.js`、`chat.html`、`console.css`、`identity-admin.js` | 五页复用控制台壳；渲染服务端动作；拒绝页不发请求；迟到响应丢弃；租户切换失效；未保存表单守卫；`#view-<id>` 直达 |
| 测试 | `tests/test_personal_console_pages.py`、`test_personal_console_web.py`、`test_personal_console_transport.py`、`test_personal_console_acceptance.py`、`test_personal_console_frontend.cjs`/`.py`、`test_personal_console_browser.cjs`/`.py`、`test_personal_resource_config.py` | 见下 |

## 1. 三态分离与权威动词（8.1）

`python -m pytest tests/test_personal_console_pages.py -q` → **通过**。
覆盖：五页 `scope=self` 且 `available/read_allowed`；`states` 恒为
`{read, config, execution}`；渠道页 `execution=false` 而 `config=true`
（无已验收类型）；`PERSONAL_RUNTIME_ACCEPTED_TYPES` 注入 `feishu` 后
`execution=true`；动词子集固定；菜单未授权时 `actions/states` 全部清零；
`admin.*` 页面 scope 与语义不变（`test_admin_pages_are_unaffected_by_the_personal_projection`）。

## 2. 壳与作用域（8.2）、目录/搜索/分页（8.3）

- `node --test tests/test_personal_console_frontend.cjs` → **52/52 通过**。
  覆盖：五视图注册与 `registerConsoleView` 契约；视图 ↔ `console_pages` 键一一对应；
  端点全部落在个人面（`/api/agents`、`/api/personal/channels`、
  `/api/personal/resources`、`/api/memory/personal`），无公共维护路径；
  `personalActionRequest` 的动作名映射（`edit→update`、`bind→start_binding`、
  `unbind→unlink`）与 `expected_version`；表单字段（渠道两步表单含凭证与口令）；
  搜索/总数/分页纯函数。
- 后端过滤证据：`test_personal_console_transport.py`
  `test_a_member_sees_only_the_resources_they_are_granted`、
  `test_a_save_lands_on_the_callers_own_row_only`、
  `test_another_member_never_sees_the_saved_configuration` →
  **通过**（个人参数保存只写本人行；列表按当前资源授权过滤）。

## 3. 侧栏 / 直达 / 未保存 / 租户切换 / 403-503 / 迟到响应（8.4）

- `test_personal_console_frontend.cjs`：`#view-` 直达、脏表单守卫
  (`__personalConsoleDirtyGuard__`)、租户切换 `invalidatePersonalViews`、
  拒绝页不发起请求、迟到响应按 generation 丢弃 → **通过**。
- `test_personal_console_web.py`：403 渲染、服务端错误码透出 → **通过**。

## 4. 文案与快照（8.5）

- `node --test tests/test_console_i18n_parity.cjs` → **5/5 通过**；
  新增键已同步进 `tests/fixtures/console_i18n_snapshot.json`
  （`personal_channels_credentials`、`personal_channels_password`，zh/zh-Hant/en 三语）。
- 文案口径：`test_the_personal_copy_never_describes_member_capability_as_system_administration`
  断言三语均无「需要系统管理权限 / requires system administration」这类把成员能力说成管理权限的表述
  （`personal_scope_hint` 为否定式表述，允许出现）。

## 5. 验收矩阵（8.6）

`python -m pytest tests/test_personal_console_acceptance.py -q` → **19 passed**：

| 任务要求 | 用例 |
| --- | --- |
| 默认 member | `DefaultMemberPageTests`：内建 `member` 角色开齐五页且无管理页；`BUILTIN_MENU_DEFAULTS` 与页面注册表不漂移 |
| 多个角色并集 | `RoleUnionTests`：两角色各持一半菜单 → 并集放开；菜单授权与功能权限来自**不同**角色仍成立；仅菜单无权限 → `no_permission` 负例 |
| 显式菜单限制 | `ExplicitMenuRestrictionTests`：带菜单授权的角色被绑定到该集合，集合外页面 `menu_not_granted` 且动作/状态清零；不带菜单授权的历史角色仍按功能权限放行 |
| 目录可读但执行关闭 | `ReadableCatalogClosedExecutionTests`：渠道页可读、`execution=false`、接口返回空列表；注入已验收类型后 `execution=true`；空目录页可读且 `config/execution=false` |
| 直接 API 越权 | `DirectApiEscalationTests`：公共 `/api/channels` 对成员拒绝；只读授权保存被拒且无落库；请求体伪造 owner 被忽略；他人个人渠道实例 `update` 被拒（正例：本人可改）；他人私有智能体删不掉；成员视图不含他人智能体 |
| 桌面/窄屏交互 | 见 §6 |

另外 `tests/test_personal_console_transport.py`、`tests/test_personal_resource_config.py`、
`tests/test_personal_console_web.py` → **全部通过**（无会话/无租户/跨租户拒绝；跨 owner 拒绝）。

## 6. 真实浏览器桌面 / 窄屏（8.6）

`NODE_PATH=/tmp/cow-pw/node_modules PLAYWRIGHT_BROWSERS_PATH=/tmp/cow-pw/browsers \
node tests/test_personal_console_browser.cjs` → **10/10 场景通过**：

1. 桌面 1440×900：`我的` 分组列出五页且文案正确；
2. 逐个点击五页均渲染自身页面，且全程未请求 `/api/channels`、`/api/tenant/channels`；
3. 渠道页状态行报告 `read · config · execution_closed`（执行关闭被明说）；
4. 记忆行只渲染服务端签发的 `edit/delete` 两个动词，页脚总数正确；
5. 搜索过滤后行与总数同步变化；
6. 直接链接 `#view-personal-memory` 直达并只请求一次消费端；
7. 窄屏 390×844：侧栏初始隐藏、可由 `#menu-toggle` 打开且遮罩出现；
8. 窄屏选择个人页面后抽屉自动关闭；
9. 窄屏页面正文宽度不超出视口且仍可搜索；
10. 菜单被拒的页面由导航层直接拒绝（落在 `#view-unavailable`），
    **不挂载页面、不请求 `/api/memory/personal`**。

本轮浏览器验证同时发现并修复了两个只在真实运行时暴露的缺陷：

- **动作名不一致**：渠道编辑/绑定按钮发出的 `action` 与实例接口的
  `update` / `start_binding` / `unlink` 不同名，原先会被当作未知动作（HTTP 200 + 错误体）；
  已在前端做显式映射并由前端契约测试锁定。
- **惰性容器不可见**：`navigateTo` 在加载器创建容器**之前**就切换 `.active`，
  fork 视图的容器因此永远隐藏。已在 `console.js` 抽出 `_activateViewContainer`，
  并在注册视图加载完成后（且仍是当前视图时）重新应用激活态。

## 7. 回归

- `node --test tests/test_personal_console_frontend.cjs tests/test_agent_workbench_frontend.cjs
  tests/test_console_workspace_frontend.cjs tests/test_tenant_channel_card_frontend.cjs
  tests/test_identity_admin_frontend.cjs tests/test_console_upload_frontend.cjs
  tests/test_console_i18n_parity.cjs tests/test_sidebar_session_archive_frontend.cjs
  tests/test_sidebar_session_rename_frontend.cjs tests/test_tenant_archive_frontend.cjs
  tests/test_tenant_default_agent_frontend.cjs tests/test_execution_permission_ui.cjs`
  → **181 passed / 0 failed**（另 1 项为误入的 `.py` 文件，不计）。
- `python -m pytest tests/test_route_registry.py tests/test_identity_web_handlers.py
  tests/test_agent_web_management.py tests/test_knowledge_web.py
  tests/test_tenant_channel_instances_service.py tests/test_tenant_channel_mutations.py
  tests/test_platform_file_browsing.py tests/test_web_chat_boundary.py -q`
  → **197 passed**。
- 与本 change 无关的既有失败（改动前即存在，已用 `git stash` 对照确认）：
  `tests/test_session_history_frontend.cjs` 单跑即 36/36 失败；
  `tests/test_sidebar_account_frontend.cjs` 5 项（期望已退役的 legacy/unknown 模式）；
  `tests/test_appearance_browser.cjs` 在 HEAD 版本 `console.js` 上同样启动失败（`#app` 隐藏）。

## 8. 尚未通过 / 待验收

- **个人渠道真机执行**：`PERSONAL_RUNTIME_ACCEPTED_TYPES` 仍为空集，
  默认保持个人执行关闭；页面据此显示 `execution closed`。飞书 / 企业微信 / 钉钉
  的真机接入与消息投递属任务 7.5 的待验收范围，未通过前不放行。
- 桌面/窄屏的**视觉细节**（配色、对比度）沿用既有 `appearance` 套件，
  本 change 未新增视觉基线；窄屏交互已在真实 Chromium 中验证（§6）。
