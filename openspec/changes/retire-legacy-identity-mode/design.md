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
- **前端/Desktop**：`channel/web/static/js/console.js`（`_identityModeState`、legacy 登录分支、`X-Branding-CSRF`、`permission_mode_source=config`）、`desktop/src/renderer/src/api/client.ts:38`（`cow_auth_token`）、`desktop/.../BasicSettings.tsx:614-628`（`web_password`）。

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

保留 `auth_handlers.py` 的 `Db*` 类名以缩小改动面（仅删 `_is_database()` 守卫）；`_require_platform_console` 统一返回平台上下文并移除调用点的 `platform_ctx or ctx` 垫片；删除 `refuse_legacy_after_migration` 与 `startup_hooks` 迁移守卫（子代理清点报告已登记 `tests/test_upstream_core_seams.py` 对 guard 的约束需同步移除）。

## Risks / Trade-offs

- [旧 `/auth/*`、`cow_auth_token`、`external_api_token` 客户端全部失效] → 破坏性切换是既定前提；提供明确拒绝码与迁移文档，首启初始化保证新装可用。
- [删除「无身份放行」后，某些后台/线程路径可能因缺身份而失败] → 统一失败关闭并记录可诊断告警；执行隔离与 fail-closed 已有规范兜底，逐消费者回归。
- [自动初始化密码泄漏] → 仅一次性输出、文件权限收紧、强制改密、不写日志/审计正文；初始化失败即拒绝业务。
- [大范围删除导致回归] → 按阶段设门槛、逐阶段回归；保留 database 既有语义不动，仅去分支。
- [服务账号绕过 Membership] → 规范明确必须为真实 User + 有效 Membership + grants，平台 all 不得绕过租户与资源授权。
- [跨 21 个既有规范的措辞清理遗漏] → 本 change 已对含 legacy 强制条款/场景的规范出 delta；实现阶段以 `openspec validate --strict` 与 grep 复核。

## Migration Plan

1. **前置切片**：确认 `execution-isolation`、`credential-management`、`resource-quota`、`audit-log` 的 database 侧验收不因本 change 退化。
2. **能力沉淀**（阶段 1）：先落地 database 侧 Desktop 登录、服务账号 API、平台级文件根、显式渠道实例、首启自动初始化；此阶段不删除 legacy，保持可回归。
3. **认证面切换**（阶段 2）：`web_channel.py` 删 legacy `/auth/*`、HMAC、query token、brand CSRF；`auth_handlers.py` 去守卫；前端/Desktop 去 legacy 分支。
4. **模式退场**（阶段 3）：`config.py` 去 `identity_mode`；旧模式拒绝启动；删除迁移守卫与 `is_database_mode()` 早退/直通。
5. **清理与验收**（阶段 4-5）：删死代码，重写 legacy 断言测试，新增覆盖，三语文档口径，跑全量回归与安全/隔离验收。
6. **恢复策略**：无运行时回退。部署前对 `identity.db` 与数据目录做快照；回退需回到旧构建并恢复快照（`identity_mode` 已移除，旧构建需用备份配置）。

## Open Questions

- 平台级文件根的配置键名与一次性密码输出文件的精确路径/权限，可在实现时按既有配置约定确定，不影响规范与任务拆分。
- 服务账号密钥的鉴权头形式（`Authorization: Bearer` 与 `X-Api-Key` 的选择）可在实现时对齐既有 API 约定，不影响规范。
