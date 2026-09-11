## Context

当前实现同时承载 `legacy` 与 `database` 两套身份与认证路径，模式由 `config.py:276-279` 的 `identity_mode` 决定（默认 `legacy`）。主要接入点：

- **模式判定源**：`channel/web/web_channel.py:3046-3047`（`_is_database_identity`）、`channel/web/auth_handlers.py:38-44`、`channel/web/tenant_workspace.py:44`、`auth/http_policy.py:53-56`、`auth/ratelimit.py:241-243`、`agent/permission/isolation.py:61-67`、`channel/external_identity.py:38-42`、`common/startup_hooks.py:83,119`、`auth/store.py:59-71`。
- **legacy 认证实现**在 `channel/web/web_channel.py`：`AuthCheckHandler`(3112)、`AuthLoginHandler`(3127)、`AuthLogoutHandler`(3152)、`_create_auth_token`(222)、`_verify_auth_token`(233)、`_check_auth`(283，database 分支优先、legacy 回退)、`_get_bearer_token`(255)、`_get_query_token`(270)、`_require_platform_console`(342)。
- **database 处理器**在 `channel/web/auth_handlers.py`（`Db*` 类），legacy 模式下 legacy 类委派给它们；其中有大量 `_is_database()`/`not_database` 守卫（353/377/421/444/471/492/616/653/683）。
- **品牌**：`web_channel.py:4085-4246` 的密码派生 CSRF 与 legacy 写入门。
- **文件服务**：`web_channel.py:1180-1212` 的 `_serve_allowed_roots` / `web_file_serve_root`（默认 `~`，`/` 放开全盘）；database 侧已有 `_db_file_serve_roots`(3368)。
- **执行授权**：`agent/permission/isolation.py:61-86,150-151,340-352`；`agent/protocol/agent_stream.py:174-183,2050-2079`。
- **渠道**：`channel/channel_instances.py:126-161,246-250,288-370,745-749`（`legacy` 字段、`bootstrap_legacy_instances`、`channel_type` 合成）。
- **待办**：`agent/todo/service.py:190-196,365-372`、`agent/tools/todo/todo_tool.py:218-227`、`channel/web/todo_handlers.py:136-217`。
- **启动/迁移**：`app.py:597-607,757`、`common/startup_hooks.py:71-101`。
- **前端/Desktop**：`channel/web/static/js/console.js`（`_identityModeState`、legacy 登录分支、`X-Branding-CSRF`、`permission_mode_source=config`）；Desktop 共 8 个文件含 `web_password`/`cow_auth_token`：`desktop/src/main/python-manager.ts`、`desktop/src/renderer/src/{api/client.ts,components/LoginGate.tsx,hooks/useBackend.ts,App.tsx,types.ts,i18n.ts,pages/settings/BasicSettings.tsx}`。
- **handler 双轨**：`web_channel.py` 内约 62 处 `_require_auth()`、约 25 处 `if ctx is None` legacy 分支；另有 fork 文件 `channel/web/admin_overview.py` 直接导入 `_require_auth` 并在 database 分支外返回 `_overview_payload(None)`。

约束：无存量安装，允许破坏性切换；不保留双模式过渡；身份真值唯一归属 `identity.db`；机器主体不得伪造 Membership；未验收的高风险消费者不得借本 change 放开。

## Goals / Non-Goals

**Goals:**

- 使 `database` 成为唯一身份模式，删除 `legacy` 认证/授权/布局分支，消除「无身份即放行」兜底。
- 把 legacy 能力沉淀到 database：单机首启自动初始化、Desktop 登录、外部 API 服务账号、平台级文件根、显式渠道实例、待办租户归属。
- 认证面收敛为单一可信来源 `auth/credential.select_credential`（`cow_session` cookie 或同值 Bearer）。
- 保持既有 database 多租户语义（逐请求授权、租户选择、会话撤销）不变。

**Non-Goals:**

- 不新增 SSO、用户自助绑定或外部 IM 扫码建号。
- 不引入 `database_runtime_enabled` 之类的临时模式开关，也不提供回退 legacy 的运行时路径。
- 不改变既有 ExecutionRun、scheduler 兼容接口与 Agent Registry/Bridge 契约。
- 不新开未经 `execution-isolation`/`credential-management`/`resource-quota`/`audit-log` 验收的高风险消费者。

## Decisions

### 决策 1：模式唯一化与启动拒绝，而非保留双模式

系统只读 `database`；`config.py` 删除 `identity_mode` 默认值与注释，启动检测到显式旧模式（`identity_mode=legacy`）时拒绝启动并提示。**替代方案**：保留键但仅接受 `database`（仍留下"模式"概念与死配置）；或保留 legacy 代码做回退（与目标冲突、双分支维护成本高）。选择前者以彻底移除分支。

### 决策 2：认证收敛到 `credential.select_credential`，删除 HMAC/共享密码/query token

保留 `auth/credential.py` 的 cookie/bearer 选择与 `mixed_credentials` 语义，删除 `web_channel.py` 中 `_create_auth_token`/`_verify_auth_token`/`_check_auth` 的 legacy 回退与 `_get_query_token`。Desktop 以登录接口返回的会话值作 Bearer（`credential.py` 已支持），无需新协议。**替代方案**：为 Desktop 设计独立机器 token（引入第二真值，违反单一凭据来源）。

### 决策 3：首启自动初始化，而非 CLI 强制或复用 `web_password`

`identity.db` 无平台管理员时，启动阶段在单事务内创建默认租户、平台管理员、内置角色与组织根；初始密码随机生成、一次性输出（控制台 + 受权限保护的一次性文件）、强制改密。**替代方案**：要求先跑 `cow management bootstrap`（多一步、单机体验差）；或把 `web_password` 降级为初始化种子（保留共享密码字段，语义纠缠）。初始化幂等，失败即拒绝进入业务。

### 决策 4：外部 API 用服务账号真实 User + 加密密钥，而非纯资源凭据或会话 Bearer

在 `identity.db` 建真实服务账号 `User`，赋予角色/grants；API 密钥按 `credential-management` 规则加密存储、只出掩码、可轮换、撤权即时失效；请求解析为该用户执行。**替代方案**：不建 User 的纯凭据绑定（需另造服务主体与执行权模型，易绕过 Membership 约束）；外部集成走短会话 Bearer（对长期集成不友好）。选择前者以复用既有 RBAC 与审计。

### 决策 5：文件服务改为平台级只读根 + 租户作用域

默认根改为部署数据目录，平台管理员只读浏览并审计；租户成员限本租户共享根与授权 Agent workspace。**替代方案**：保留 `web_file_serve_root` 任意根（默认 `~` 或 `/` 风险过高）；或彻底取消主目录浏览（与"保留该能力"目标不符）。

### 决策 6：渠道实例显式登记，删除 `channel_type` 隐式合成

启动与入站只认显式实例记录（平台级/租户级），删除 `bootstrap_legacy_instances` 与 `channel_type` 回退。租户侧类型仍受 `tenant-channel-configuration` 的"已就绪多实例"限制。**替代方案**：保留隐式合成（与显式归属规范冲突，且新装无历史可迁）。

### 决策 7：待办统一租户作用域，空 owner 历史归属初始化管理员

删除 `TodoActor.legacy()` 与 local-owner 语义；既有 owner 为空的待办在初始化时归属首启管理员。**替代方案**：丢弃空 owner 待办（数据丢失）；或保留 local-owner 分支（与新主体模型冲突）。

### 决策 8：实现范围与命名

保留 `auth_handlers.py` 的 `Db*` 类名以缩小改动面（仅删 `_is_database()` 守卫）；`_require_platform_console` 统一返回平台上下文并移除调用点的 `platform_ctx or ctx` 垫片。

### 决策 9：删除批次按「fork 自有 → 上游原生 → 立即同步排练」排序

`legacy` 认证在 master 中**原生存在**（`config.py:265` 的 `web_password`、master `web_channel.py` 的 `_get_web_password`/`_create_auth_token`/`_verify_auth_token`/`_check_auth`/`AuthLoginHandler`/`AuthLogoutHandler`），且位于上游高频文件（master 提交数：`web_channel.py` 277、`console.js` 213、`config.py` 259、`app.py` 70）。因此删除顺序必须是：

1. **先删 fork 自造文件**（master 提交数 0，零上游冲突）：`auth/store.py`、`auth/http_policy.py`、`auth/ratelimit.py`、`agent/permission/isolation.py`、`channel/external_identity.py`（mode 分支）、`channel/web/auth_handlers.py`（守卫）。
2. **再删上游原生文件**，收成一个紧凑批次：`channel/web/web_channel.py`（legacy `/auth/*`、HMAC、brand CSRF、**全部 handler 的 `_require_auth`/`ctx is None` 收敛**）、`config.py`（`identity_mode`）、`channel/web/static/js/console.js`、`channel/web/chat.html`（登录遮罩去共享密码、用户名常显）、`app.py`、`channel/channel_instances.py`、Desktop 八文件。
3. **批次完成后立即**跑 `scripts/sync-from-master.sh`，用结果重生成 `scripts/conflict-baseline.txt` 并按决策 14 登记 `keep-fork`/`seam:`/`merge-docs`。

**替代方案**：按原阶段划分（认证面 → 模式退场 → 清理）连续改上游文件，战线覆盖多个阶段，分歧窗口被放大。选择紧凑批次以最小化与持续更新的 master 的偏差。

### 决策 10：前端 fork 标记走 fragment 挂载，不原地重写上游登录块

`console.js` 是上游改动量最大的文件。登录/账号相关的 fork 定制 SHALL 沿用仓库既有先例：`chat.html` 通过 `data-fork-fragment` 挂载点 + `static/js/fragments.js` 装载 `static/fragments/*.html`，由 `tests/test_fork_fragments.cjs` 强制「fork 标记不得内联在上游核心文件」。legacy 登录分支的移除属于删除上游代码（不是新增 fork 标记），因此原地删除上游 `legacy` 分支是允许的；但任何**新增**的 fork 登录/账号 UI MUST 走 fragment 挂载，MUST NOT 再内联进 `console.js`/`chat.html`。

**替代方案**：在 `console.js` 原地新增 fork 登录 UI。与既有分片契约冲突，会持续制造合并冲突。

### 决策 11：替换 guard 约束测试，删除迁移守卫但保住 tenancy 迁移

原 `tasks.md` 4.2 写「同步移除 `tests/test_upstream_core_seams.py` 对 guard 的约束」，方向过于粗暴。正确做法：

- **删除**：`auth/store.refuse_legacy_after_migration` 与 `common/startup_hooks.py` 的 legacy 一致性守卫（`_identity_mode_consistency`）及其断言。
- **替换而非删除**：`test_upstream_core_seams.py` 的形如 `test_legacy_mode_returns_none_so_upstream_fallback_runs`（断言 `database_mode=False → None`）与 `test_an_empty_tenant_scope_is_refused_in_database_mode`（调用 `resolve_tenant_workspace_root(database_mode=True)`）必须改写：前者删除（legacy 分支不存在），后者改为不传 mode 的新契约。新不变量为「显式 `identity_mode=legacy` 拒绝启动」+「`app.py` 仍只经 `run_startup_hook` 执行守卫」。
- **必须保住**：`_tenant_conversation_backfill`（`_migrate_conversation_tenancy`）与 tenancy 相关的 schema/回填逻辑不得删除；`test_conversation_schema_seam.py` 的接缝不变量保持通过。

### 决策 12：路由清单与授权策略同步更新（`REMOVED` 追加，非删行）

删除 `route_registry.py:103-105` 中登记为 `upstream` 的三条 `/auth/*`（`/auth/login`、`/auth/check`、`/auth/logout`）时，必须同步更新权威清单与授权策略派生，并保证 `tests/test_route_registry.py`、`scripts/check-route-coverage.py` 的三腿不变量校验通过。

`scripts/route-baseline.txt` 是 **append-only**：底部解析节覆盖历史行。删除已登记路由的正确做法是**追加**形如 `/auth/login	POST	REMOVED	-` 的行（先例：`/api/sessions/(.*)	GET	REMOVED`），**不得**从历史节删行。`FrozenBaselineEquivalenceTests` 对 `REMOVED` 断言派生策略中该 method 为 `None`。**替代方案**：只删 handler 不改清单，会导致清单引用不存在的 handler、三腿校验失败；或直接删基线历史行，会破坏 append-only 契约与等价性测试。

### 决策 13：handler 级认证终态——删 `_require_auth`，以路由策略 + `_db_scope` 为准

`enforce_http_policy`（由 `route_registry` 派生）已在 handler 之前完成认证与授权。终态是：

1. **删除** `_require_auth` / `_check_auth` 及其全部调用点（`web_channel.py` 约 62 处 + `admin_overview.py` 导入）。
2. **保留** `_db_scope()`（或等价的 `_require_context`），且 **删除** 所有 `if ctx is None: <legacy 路径>` 分支；`ctx` 不可解析时一律失败关闭（401/403/400/503，按既有规范）。
3. **路由策略为第一道门**：`public` 壳路由（如 `/chat`、`/admin` GET）不要求会话即可渲染文档；`tenant`/`platform`/`personal` 由闸强制；`closed` 路由保持不可达或按清单策略。
4. **不得**在 handler 内再实现一套共享密码/HMAC 第二道门。

工作量按分组收敛（见 `tasks.md` 阶段 3），不得写成一条脚注。**替代方案**：保留 `_require_auth` 作「会话存在性」检查（仍双轨、且与闸重复）；或只删 helper 不改 62 个调用点（运行时 NameError）。

### 决策 14：冲突处置词表——文件内删除用 `keep-fork`/`seam:`，不用 `keep-deletion`

`scripts/conflict-baseline.txt` 与 `scripts/sync_report.py` 的词表：

| disposition | 适用 |
|---|---|
| `keep-deletion` | 整文件 `DU`（fork 删文件、上游改同路径）；且必须镜像进 `DELIBERATE_REMOVALS` |
| `keep-fork` | `UU` 文件内冲突，fork 侧胜出并独立标注块 |
| `seam:<tasks>` | 由本 change 的接缝/任务收口 |
| `merge-docs` | 纯文档双侧编辑 |

本 change **不删除任何整份上游文件**，只删/改文件内代码段，因此新增冲突 SHALL 登记为 `keep-fork` 或 `seam:<tasks>`；文档改动登记 `merge-docs`。**MUST NOT** 对本 change 的文件内删除使用 `keep-deletion`，**MUST NOT** 改动 `DELIBERATE_REMOVALS`（除非将来真有整文件删除）。

## 上游合并姿态（master 作为独立分支持续更新）

本 change 属于 fork「删除/修改上游原生功能」这一最高冲突代价类别，必须按 `fork-upstream-decoupling` 记录并对每条冲突给出处置。

**本 change 主动改写的上游原生 / 高频文件（需登记基线处置，`keep-fork` 或 `seam:`）**：`channel/web/web_channel.py`（含约 40 个 handler 的 legacy 分支收敛）、`channel/web/static/js/console.js`、`channel/web/chat.html`、`config.py`、`app.py`、`channel/channel_instances.py`、`channel/web/route_registry.py`（若上游同步触及）、Desktop 八文件中属上游跟踪者。

**本 change 改写的 fork 自有文件（零上游冲突，仍须清理）**：`channel/web/admin_overview.py`、`channel/web/auth_handlers.py`、`channel/web/todo_handlers.py`、`auth/{store,http_policy,ratelimit}.py` 等。

**本 change 改写的上游文档（登记 `merge-docs`）**：`docs/{,zh,ja}/channels/web.mdx`、`docs/{,zh}/guide/{quick-start,manual-install}.mdx`，以及必要时的 `docs/{,zh,ja}/releases/v2.1.0.mdx` 口径说明；`webhelp/` 派生页随源更新。

**本 change 明确不触碰、合并必须保住的上游资产（只承诺保留，不改语义）**：

| 路径 | 现状处置 | 理由 |
|---|---|---|
| `agent/memory/conversation_store.py` | 保持（`seam:6.1-6.11`） | 组合 schema 接缝，与身份模式无关 |
| `agent/tools/scheduler/integration.py` | 保持（`seam:8.15-8.16`） | 统一调度服务与身份收敛接缝 |
| `channel/web/chat.html` 的 `data-fork-fragment` 挂载点 | 保留契约（改写登录遮罩，`seam:8.8`） | fork 分片不得内联；`appearance-dialog` 挂载与装载顺序、login overlay 的 DOM id/name 契约不变 |
| `tests/test_scheduler_web_update.py` | 保持（`seam:8.17`） | 上游调度行为断言 |
| `channel/web/web_channel.py` 内的 `_import_local_file` | 保持 | 上游「按本地路径导入」能力及其 loopback + 每启动令牌校验（`conflict-baseline` 已记录 obligation） |

**既有基线**：`scripts/conflict-baseline.txt` 冻结于 `origin/master@9ad944dd × origin/rdai@dc760766`（20 冲突文件）。本 change 完成后必须重新生成，使新产生的修改类冲突逐条登记为 `keep-fork`/`seam:`/`merge-docs`，且 `scripts/check_change_deltas.py retire-legacy-identity-mode` 通过。`DELIBERATE_REMOVALS` 保持不变。

## Risks / Trade-offs

- [旧 `/auth/*`、`cow_auth_token`、`external_api_token` 客户端全部失效] → 破坏性切换是既定前提；提供明确拒绝码与迁移文档，首启初始化保证新装可用。
- [删除「无身份放行」后，某些后台/线程路径可能因缺身份而失败] → 统一失败关闭并记录可诊断告警；执行隔离与 fail-closed 已有规范兜底，逐消费者回归。
- [自动初始化密码泄漏] → 仅一次性输出、文件权限收紧、强制改密、不写日志/审计正文；初始化失败即拒绝业务。
- [约 62 处 `_require_auth` + 约 25 处 `ctx is None` 收敛遗漏导致半双轨] → 决策 13：按分组任务收敛；收尾用「不复活」断言 + 全仓 grep 复核 `_require_auth`/`ctx is None`/`web_password`。
- [服务账号绕过 Membership] → 规范明确必须为真实 User + 有效 Membership + grants，平台 all 不得绕过租户与资源授权。
- [跨 21 个既有规范的措辞清理遗漏] → 本 change 已对含 legacy 强制条款/场景的规范出 delta；实现阶段以 `openspec validate --strict` 与 grep 复核。
- [master 持续更新，删除批次战线过长导致冲突与上游功能丢失] → 按决策 9 排序（fork 自有先删、上游原生收成紧凑批次、批次后立即 `sync-from-master.sh` 排练并重生成基线）；`fork-upstream-decoupling` 新增规范明确该门槛。
- [上游合并复活共享密码或旧 token 认证] → 加「不复活」断言式回归（决策/规范已定义），合并后立即运行，以测试失败而非静默放行暴露。
- [删除 legacy 路由后基线等价性测试失败] → 决策 12：`route-baseline.txt` 追加 `REMOVED` 行，不删历史行。
- [误用 `keep-deletion` 导致 `DELIBERATE_REMOVALS` drift] → 决策 14：文件内删除只用 `keep-fork`/`seam:`；整文件删除常量不变。
- [误删上游接缝或放宽其断言] → 决策 11：guard 约束测试替换不删除，`_tenant_conversation_backfill` 与组合 schema/调度/分片接缝保持；`test_conversation_schema_seam.py`、`test_fork_fragments.cjs` 保持通过。

## Migration Plan

1. **前置切片**：确认 `execution-isolation`、`credential-management`、`resource-quota`、`audit-log` 的 database 侧验收不因本 change 退化。
2. **能力沉淀**（阶段 1）：先落地 database 侧 Desktop 八文件登录适配、服务账号 API、平台级文件根、显式渠道实例、首启自动初始化；此阶段不删除 legacy，保持可回归。
3. **fork 自有文件删除**（阶段 2，零上游冲突）：`auth/store.py`、`auth/http_policy.py`、`auth/ratelimit.py`、`agent/permission/isolation.py`、`channel/external_identity.py` 的模式分支、`channel/web/auth_handlers.py` 守卫、`channel/web/admin_overview.py` 的 `_require_auth` 与 legacy 返回。
4. **上游原生文件删除 + handler 收敛**（阶段 3，紧凑批次）：按决策 13 分组收敛 `web_channel.py` 全部 `_require_auth`/`ctx is None`；删 legacy 认证面与 brand CSRF；`config.py` 的 `identity_mode`；`console.js`/`chat.html`/`app.py`/`channel_instances.py`；Desktop 八文件收口；路由清单删除并**追加** `REMOVED` 行。**批次完成后立即**跑 `scripts/sync-from-master.sh`，重新生成 `scripts/conflict-baseline.txt`，按决策 14 登记 `keep-fork`/`seam:`/`merge-docs`。
5. **清理与验收**（阶段 4-5）：删死代码，替换 guard 约束测试，重写 legacy 断言测试，新增「不复活」断言，三语 + webhelp 文档口径，跑全量回归与安全/隔离验收，跑 `check_change_deltas.py` 与各接缝/路由不变量测试。
6. **恢复策略**：无运行时回退。部署前对 `identity.db` 与数据目录做快照；回退需回到旧构建并恢复快照（`identity_mode` 已移除，旧构建需用备份配置）。

## Open Questions

已决议（实现参数）：

- **平台级文件根配置键**：`platform_file_root`；缺省为 `get_data_root()`；MUST NOT 默认 `~` 或 `/`。显式配置 `/` 时仅平台管理员只读，且须记审计。
- **首启一次性密码文件**：`{get_data_root()}/.bootstrap_admin_password`，权限 `0600`；控制台 `print` 同步输出（禁止 `logger`）；强制改密成功后删除该文件。
- **服务账号 API 鉴权头**：`Authorization: Bearer <api_key>`（与既有 `credential.select_credential` / 会话 Bearer 同形）；不支持 URL query token。
- **首启默认主体**：租户 code=`default`、管理员 username=`admin`、display=`Administrator`、`shared_root={get_data_root()}/tenants/default`。
