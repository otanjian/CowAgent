## 1. 前置与门槛

- [ ] 1.1 核对 `execution-isolation`、`credential-management`、`resource-quota`、`audit-log` 的 database 侧切片验收证据是否在位；缺口先在对应 capability 补齐，未通过不得进入阶段 1 的高风险消费者任务
- [ ] 1.2 对 `identity.db` 与数据目录做部署前快照，并确认目标实例确无存量 legacy 安装（无存量方可破坏性切换）
- [ ] 1.3 确定平台级文件根配置键名与首启一次性密码输出文件的精确路径/权限（默认数据目录与 0600、首登后删除），落为实现参数说明
- [ ] 1.4 本 change 不设 feature flag；以「配置移除 + 旧模式拒绝启动」作为模式切换门槛，验收证据在案后方可进入下一阶段

## 2. 阶段 1：能力沉淀（保留 legacy 代码，保持可回归）

- [ ] 2.1 `database-bootstrap`：在启动序列（`app.py`、`common/startup_hooks.py`）实现「无平台管理员即单事务初始化默认租户 + 平台管理员 + 内置角色 + 组织根」，幂等且失败拒绝进入业务
- [ ] 2.2 `database-bootstrap`：实现随机初始密码一次性输出（控制台 + 受权限保护文件）、强制改密标记，禁止写入日志正文与审计正文
- [ ] 2.3 `database-bootstrap`：补测试——首启初始化、已初始化不重复、初始化失败回滚并拒绝业务、受限会话仅可改密/退出
- [ ] 2.4 `service-account-api-access`：在 `identity.db` 支持服务账号真实 `User`（角色/grants），复用 `auth/service.py` 既有账号与授权模型
- [ ] 2.5 `service-account-api-access`：按 `credential-management` 实现 API 密钥加密存储、掩码投影、轮换与撤权即时失效，并接入审计
- [ ] 2.6 `service-account-api-access`：在 `channel/web/openai_api.py` 解析凭据为服务账号并按其权限执行，无效/撤权返回 401、身份库故障 503，禁止 query token 与匿名回退
- [ ] 2.7 `service-account-api-access`：补测试——创建只出掩码、轮换/撤权后旧密钥失效、无权限拒绝、跨租户拒绝
- [ ] 2.8 `platform-file-browsing`：新增平台级只读文件根（默认数据目录），仅平台管理员可浏览、拒绝写入、记审计（改造 `web_channel.py:1180-1212`）
- [ ] 2.9 `platform-file-browsing`：把租户成员文件访问收敛到 `_db_file_serve_roots`/本租户共享根与授权 Agent workspace，缺租户上下文 403、跨租户不可见
- [ ] 2.10 `platform-file-browsing`：补测试——平台管理员只读、普通成员 403、跨租户 404/403、默认根不含 `~` 或 `/`
- [ ] 2.11 Desktop 适配：`desktop/src/renderer/src/api/client.ts` 去除 `cow_auth_token` localStorage，改数据库登录并以会话值作 Bearer
- [ ] 2.12 Desktop 适配：`desktop/.../BasicSettings.tsx` 移除 `web_password` 编辑入口，账号安全改走本人改密流程
- [ ] 2.13 Desktop 适配：补测试——数据库账号登录 + Bearer 调用成功、旧 token 被拒
- [ ] 2.14 渠道实例显式登记：`channel/channel_instances.py` 删除 `channel_type` 回退与 `bootstrap_legacy_instances` 合成，启动/入站只认显式实例记录
- [ ] 2.15 渠道实例显式登记：补测试——显式实例启动、仅旧 `channel_type` 不启动、作用域越界拒绝
- [ ] 2.16 待办归属：删除 `TodoActor.legacy()` 与 local-owner 路径（`agent/todo/service.py`、`agent/tools/todo/todo_tool.py`、`channel/web/todo_handlers.py`）
- [ ] 2.17 待办归属：把 owner 为空的历史待办在初始化时归属首启管理员，并补迁移测试
- [ ] 2.18 阶段 1 门槛：跑 database 侧回归，确认新增能力可用且 legacy 行为未破坏；证据在案后进入阶段 2

## 3. 阶段 2：认证面切换

- [ ] 3.1 `channel/web/web_channel.py`：删除 legacy `/auth/*` 实现（`AuthCheckHandler`、`AuthLoginHandler`、`AuthLogoutHandler`）与 `_create_auth_token`、`_verify_auth_token`、`_get_bearer_token`、`_get_query_token`
- [ ] 3.2 `_check_auth` 收敛为仅 `auth/credential.select_credential`（cookie/bearer，保留 `mixed_credentials` 语义），删除 legacy 回退
- [ ] 3.3 `_require_platform_console` 统一返回平台上下文，移除调用点 `platform_ctx or ctx` 垫片（`web_channel.py:8661` 等）
- [ ] 3.4 `channel/web/auth_handlers.py`：删除所有 `_is_database()`/`not_database` 守卫，`Db*` 成为唯一处理器（保留类名）
- [ ] 3.5 `channel/web/admin_handlers.py`：删除等价 database 守卫与 legacy 分支
- [ ] 3.6 品牌写入：删除 `web_channel.py` 密码派生 Brand CSRF 与 legacy 写入门（`_branding_write_allowed`、`_branding_auth_token`、`_branding_csrf_token`、`_branding_csrf_ok`、`_branding_require_write`、`_branding_management_payload` 的 legacy 部分），统一走 `_branding_require_platform_admin`
- [ ] 3.7 前端：`channel/web/static/js/console.js` 删除 `_identityModeState`/legacy 兼容、legacy 登录分支、`X-Branding-CSRF`、`permission_mode_source=config` 分支与 legacy i18n 文案
- [ ] 3.8 前端：`channel/web/static/js/i18n/tasks-records.js` 等清理「legacy 模式」文案
- [ ] 3.9 补测试——旧 `/auth/*`、`cow_auth_token`、URL query token 一律 401/404；Bearer 会话登录成功；品牌写入仅平台管理员
- [ ] 3.10 阶段 2 门槛：认证面回归与安全用例在案，确认无匿名/共享密码放行路径后进入阶段 3

## 4. 阶段 3：模式退场

- [ ] 4.1 `config.py`：删除 `identity_mode` 默认值与注释；启动检测到显式 `identity_mode=legacy` 时拒绝启动并给可操作提示
- [ ] 4.2 删除 `auth/store.py:refuse_legacy_after_migration` 与 `common/startup_hooks.py:71-101` 迁移守卫，并同步移除 `tests/test_upstream_core_seams.py` 对 guard 的约束
- [ ] 4.3 去掉各消费点的 `is_database_mode()`/`identity_mode` 早退与直通：`agent/permission/isolation.py`、`auth/http_policy.py`、`auth/ratelimit.py`、`channel/external_identity.py`、`channel/chat_channel.py`、`channel/channel_instances.py`、`channel/web/tenant_workspace.py`
- [ ] 4.4 删除 `agent/protocol/agent_stream.py` 的 `_db_mode()` 分支与 `web_legacy_authenticated` 全链路（`common/runtime_identity.py`、`channel/chat_channel.py`）
- [ ] 4.5 删除所有 `ctx is None => 放行`：`_db_scope`、`_current_db_identity`、`_require_read_permission`、`_require_resource_action`、`_require_session_owner`、`_require_owned_session`、`_register_owner_scope` 等改为 401/403
- [ ] 4.6 补测试——显式 legacy 配置拒绝启动、无身份 401、缺租户 403/400、身份库故障 503
- [ ] 4.7 阶段 3 门槛：确认无任何 `identity_mode`/`is_database_mode` 运行分支残留后进入阶段 4

## 5. 阶段 4：清理与文档

- [ ] 5.1 删除清点报告第 9 节列出的 legacy 断言测试，并将为隔离环境而 pin `legacy` 的测试改写为 database 环境
- [ ] 5.2 复查全仓 `legacy`/`identity_mode`/`web_password`/`external_api_token` 残留（代码、配置模板、前端、Desktop）
- [ ] 5.3 更新 `config-template.json` 与部署脚本（`scripts/`、`webhelp/assets/deploy/`）去除共享密码与旧模式说明
- [ ] 5.4 更新三语文档 `docs/{,zh,ja}/channels/web.mdx` 等：品牌写入口径、Desktop 登录、外部 API 服务账号、文件服务作用域
- [ ] 5.5 运行 `openspec validate retire-legacy-identity-mode --strict`，确认规范 delta 与实际实现一致

## 6. 阶段 5：验收

- [ ] 6.1 全量回归测试通过（含新增首启初始化、服务账号密钥生命周期、平台级文件根隔离、渠道显式登记、Desktop Bearer）
- [ ] 6.2 安全验收：无匿名/共享密码/旧 token 放行路径；服务账号与渠道凭据不落明文；跨租户访问按不可见拒绝
- [ ] 6.3 执行隔离与 fail-closed 验收：身份不可解析时拒绝执行并记录告警
- [ ] 6.4 恢复演练：按快照恢复路径验证，确认不自动使用共享密码开放数据
- [ ] 6.5 归档 change 并复核主规范无遗留 legacy 强制条款
