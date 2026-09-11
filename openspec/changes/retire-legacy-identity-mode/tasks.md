## 1. 前置与门槛

- [x] 1.1 核对 `execution-isolation`、`credential-management`、`resource-quota`、`audit-log` 的 database 侧切片验收证据是否在位；缺口先在对应 capability 补齐，未通过不得进入阶段 1 的高风险消费者任务
- [x] 1.2 对 `identity.db` 与数据目录做部署前快照，并确认目标实例确无存量 legacy 安装（无存量方可破坏性切换）
- [x] 1.3 确定平台级文件根配置键名与首启一次性密码输出文件的精确路径/权限（默认数据目录与 0600、首登后删除），落为实现参数说明
- [x] 1.4 记录当前合并基线：确认 `scripts/conflict-baseline.txt` 冻结于 `origin/master@9ad944dd × origin/rdai@dc760766`，并跑一次 `.venv/bin/python scripts/check_change_deltas.py retire-legacy-identity-mode` 记录初始对照（本 change 已点名 4 条 seam 路径）
- [x] 1.5 本 change 不设 feature flag；以「配置移除 + 旧模式拒绝启动」作为模式切换门槛，验收证据在案后方可进入下一阶段

## 2. 阶段 1：能力沉淀（保留 legacy 代码，保持可回归）

- [x] 2.1 `database-bootstrap`：在启动序列（`app.py`、`common/startup_hooks.py`）实现「无平台管理员即单事务初始化默认租户 + 平台管理员 + 内置角色 + 组织根」，幂等且失败拒绝进入业务
- [x] 2.2 `database-bootstrap`：实现随机初始密码一次性输出（控制台 + 受权限保护文件）、强制改密标记，禁止写入日志正文与审计正文
- [x] 2.3 `database-bootstrap`：补测试——首启初始化、已初始化不重复、初始化失败回滚并拒绝业务、受限会话仅可改密/退出
- [x] 2.4 `service-account-api-access`：在 `identity.db` 支持服务账号真实 `User`（角色/grants），复用 `auth/service.py` 既有账号与授权模型
- [x] 2.5 `service-account-api-access`：按 `credential-management` 实现 API 密钥加密存储、掩码投影、轮换与撤权即时失效，并接入审计
- [x] 2.6 `service-account-api-access`：在 `channel/web/openai_api.py` 解析凭据为服务账号并按其权限执行，无效/撤权返回 401、身份库故障 503，禁止 query token 与匿名回退
- [x] 2.7 `service-account-api-access`：补测试——创建只出掩码、轮换/撤权后旧密钥失效、无权限拒绝、跨租户拒绝
- [x] 2.8 `platform-file-browsing`：新增平台级只读文件根（默认数据目录），仅平台管理员可浏览、拒绝写入、记审计（改造 `web_channel.py:1180-1212`）
- [x] 2.9 `platform-file-browsing`：把租户成员文件访问收敛到 `_db_file_serve_roots`/本租户共享根与授权 Agent workspace，缺租户上下文 403、跨租户不可见
- [x] 2.10 `platform-file-browsing`：补测试——平台管理员只读、普通成员 403、跨租户 404/403、默认根不含 `~` 或 `/`
- [x] 2.11 Desktop 适配（八文件）：`desktop/src/renderer/src/api/client.ts` 去除 `cow_auth_token` localStorage，改数据库登录并以会话值作 Bearer；`desktop/src/renderer/src/components/LoginGate.tsx` 改为 database 账号登录闸（用户名+密码），删除共享密码单字段路径
- [x] 2.12 Desktop 适配（八文件）：`desktop/src/renderer/src/pages/settings/BasicSettings.tsx` 移除 `web_password` 编辑入口，账号安全改走本人改密；同步清理 `hooks/useBackend.ts`、`App.tsx`、`types.ts`、`i18n.ts`、`desktop/src/main/python-manager.ts` 中的 `web_password`/`cow_auth_token`/`identity_mode` 分支
- [x] 2.13 Desktop 适配：补测试——数据库账号登录 + Bearer 调用成功、旧 token 被拒、LoginGate 无共享密码路径
- [x] 2.14 渠道实例显式登记：`channel/channel_instances.py` 删除 `channel_type` 回退与 `bootstrap_legacy_instances` 合成，启动/入站只认显式实例记录
- [x] 2.15 渠道实例显式登记：补测试——显式实例启动、仅旧 `channel_type` 不启动、作用域越界拒绝
- [x] 2.16 待办归属：删除 `TodoActor.legacy()` 与 local-owner 路径（`agent/todo/service.py`、`agent/tools/todo/todo_tool.py`、`channel/web/todo_handlers.py`）——阶段 2 收口；阶段 1 已加空 owner 迁移
- [x] 2.17 待办归属：把 owner 为空的历史待办在初始化时归属首启管理员，并补迁移测试
- [x] 2.18 阶段 1 门槛：跑 database 侧回归，确认新增能力可用且 legacy 行为未破坏；证据在案后进入阶段 2

## 3. 阶段 2：fork 自有文件删除（零上游冲突）

- [x] 3.1 `auth/store.py`：删除 `refuse_legacy_after_migration` 及 legacy 迁移守卫分支
- [x] 3.2 `auth/http_policy.py`：删除 legacy pass-through 分支，网关策略恒定按身份模式唯一语义
- [x] 3.3 `auth/ratelimit.py`：删除默认 `legacy` 与随模式变化的多 worker 拒绝分支
- [x] 3.4 `agent/permission/isolation.py`：删除 `database_mode()` 模式判定与 legacy 直通，隔离门恒定启用
- [x] 3.5 `agent/protocol/agent_stream.py`：删除 `_db_mode()` 分支与 `web_legacy_authenticated` 相关链路（含 `common/runtime_identity.py`、`channel/chat_channel.py`）
- [x] 3.6 `channel/external_identity.py`、`channel/chat_channel.py`、`channel/channel_instances.py`：删除 `is_database_mode()` 早退与直通
- [x] 3.7 `channel/web/auth_handlers.py`：删除所有 `_is_database()`/`not_database` 守卫，`Db*` 成为唯一处理器（保留类名）
- [x] 3.8 `channel/web/admin_handlers.py`：删除等价 database 守卫与 legacy 分支
- [x] 3.9 `channel/web/admin_overview.py`：删除对 `_require_auth` 的导入与调用；删除 `if _is_database(): ... else: return _overview_payload(None)` 的 legacy 返回，恒定走 `_require_context` + `_require_admin_console_access`
- [x] 3.10 `channel/web/todo_handlers.py`：删除 legacy actor 分支与 `_require_auth` 调用
- [x] 3.11 `channel/web/tenant_workspace.py`：删除 `database_mode=False` 返回 `None` 的 legacy 回退，`resolve_tenant_workspace_root` 恒定按租户作用域（空租户 403）
- [x] 3.12 补齐 `2.x` 中因删除而变成死分支的测试；确认本阶段文件在 master 侧提交数为 0，不产生上游冲突
- [x] 3.13 阶段 2 门槛：跑 database 侧回归 + `tests/test_upstream_core_seams.py`（先记录将被阶段 4 替换的断言），确认无上游冲突产生后进入阶段 3

## 4. 阶段 3：上游原生文件删除 + handler 收敛（紧凑批次）并立即同步排练

### 4A. 认证面与配置

- [x] 4.1 `channel/web/web_channel.py`：删除 shared-password/HMAC helpers（`_get_web_password`、`_create_auth_token`、`_verify_auth_token`、`_get_bearer_token`、`_get_query_token`、`_check_auth`、`_require_auth`）；`AuthCheck/Login/LogoutHandler` 保留为转调 `Db*` 的薄包装（路由类名兼容）
- [x] 4.2 凭据选择：会话解析仅经 `auth/credential.select_credential`（cookie/bearer，保留 `mixed_credentials` 语义）；路由闸 `enforce_http_policy` 为第一道门，handler 不再做第二道共享密码/HMAC 检查
- [x] 4.3 `_require_platform_console` 统一返回平台上下文；已移除 `platform_ctx or ctx` 垫片
- [x] 4.4 品牌写入：删除 `_branding_write_allowed`、`_branding_auth_token`、`_branding_csrf_*` 等 legacy 部分，统一走 `_branding_require_platform_admin`
- [x] 4.5 `config.py`：`identity_mode` 仅保留 database 默认；显式 `legacy` 启动拒绝
- [x] 4.6 `app.py`：确认 `_guard_identity_mode_consistency` 仍只转调 `run_startup_hook`；守卫为「旧模式拒绝启动」；保留 `_tenant_conversation_backfill` 与 `_migrate_conversation_tenancy`

### 4B. handler 级认证收敛（决策 13；本 change 主体工作量）

按组删除 `_require_auth()` 调用与全部 `if ctx is None: <legacy 路径>`；保留 `_db_scope()`/`_require_context`，`ctx` 不可解析时失败关闭。每组改完跑该组相关测试后再进下一组。

- [x] 4.7 聊天与会话组：`_require_auth` 已清；upload/voice 的 `ctx is None` legacy 放行已改为纯 database 路径
- [x] 4.8 文件组：平台/租户文件根已对齐；UploadsHandler 已无 `ctx is None` 放行
- [x] 4.9 技能/工具/记忆/调度组：database 测试与 `_db_scope` 收敛完成
- [x] 4.10 Agent 组：`platform_ctx or ctx` 已去；agent 投影 None 不再回退 legacy workbench
- [x] 4.11 渠道注册组：显式登记已落地，与闸一致
- [x] 4.12 收口核对：`_require_auth`/`_check_auth` 为 0；handler 内 legacy 放行义 `ctx is None` 已清（helpers 上防御性 None early-return 保留）

### 4C. 前端、渠道、路由、Desktop 收口

- [x] 4.13 `channel/web/static/js/console.js`：删除 `_identityModeState`/legacy 登录、`X-Branding-CSRF`、共享密码设置与 `permission_mode_source=config` 可写分支
- [x] 4.14 `channel/web/static/js/identity-admin.js`：清理陈旧 `identity_mode=database` 注释/分支
- [x] 4.15 `channel/web/static/js/i18n/tasks-records.js` 等清理「legacy 模式」身份文案
- [x] 4.16 `channel/web/chat.html`：`login-username-wrap` 常显；删除共享密码设置 UI；`tests/test_fork_fragments.cjs` 通过
- [x] 4.17 `channel/channel_instances.py`：删除 legacy `channel_type` 合成；`bootstrap_legacy_instances` 仅保留已有 roster，不再合成
- [x] 4.18 路由清单同步：保留 `/auth/*` 薄包装 handler（转调 `DbAuth*`）；在 `route_registry.py` 文档化 database-only，**不**追加 `REMOVED`（handlers 仍存在）
- [x] 4.19 Desktop 八文件收口复核：无 `web_password`/`cow_auth_token` 鉴权路径残留（仅类型字段 `identity_mode?` 响应投影）
- [x] 4.20 补测试——不复活断言覆盖 `_require_auth`/`_get_web_password` 等 helper 缺席、Auth* 薄包装、部署脚本无共享密码；旧 token/query 拒绝与 Bearer/品牌/拒启用例已由既有套件覆盖

### 4D. 同步排练与基线登记（决策 14）

- [ ] 4.21 **立即同步排练**：工作树干净后运行 `scripts/sync-from-master.sh`，用结果重新生成 `scripts/conflict-baseline.txt`
- [ ] 4.22 为新增的**文件内**修改类冲突逐条登记处置：`keep-fork` 或 `seam:<tasks>`（本 change **不**新增整文件 `DU`，故 **不得** 使用 `keep-deletion`，**不得** 改动 `scripts/sync_report.py` 的 `DELIBERATE_REMOVALS`）；文档冲突登记 `merge-docs`
- [ ] 4.23 在上表登记至少：`web_channel.py`、`console.js`、`chat.html`、`config.py`、`app.py`、`channel_instances.py`、Desktop 被触达文件、以及本 change 改写的 `docs/**`；确认 `_import_local_file` 的 obligation 仍在基线中
- [x] 4.24 跑 `scripts/check-route-coverage.py` 与 `tests/test_route_registry.py` 通过
- [ ] 4.25 阶段 3 门槛：`check_change_deltas.py` 不再报告未被点名的冲突文件；确认 master 新增路由/功能未被静默丢弃后进入阶段 4

## 5. 阶段 4：清理与文档

- [x] 5.1 替换（而非删除）`tests/test_upstream_core_seams.py` 的 guard 约束：`test_legacy_mode_returns_none_so_upstream_fallback_runs` 删除（legacy 分支不存在），`test_an_empty_tenant_scope_is_refused_in_database_mode` 改为不传 mode 的新契约；新增不变量「显式 `identity_mode=legacy` 拒绝启动」+「`app.py` 仍只经 `run_startup_hook`」
- [x] 5.2 保住 tenancy 与接缝测试：`test_conversation_schema_seam.py`、`test_scheduler_web_update.py`、`test_upstream_core_seams.py` 当前通过
- [x] 5.3 新增「不复活」断言式回归：`tests/test_no_resurrection_legacy_identity.py` 已落地
- [x] 5.4 删除/改写 pin `legacy` 的隔离测试为 database 环境（branding/openai/scenes/channels 等已改）
- [x] 5.5 复查全仓 `legacy`/`identity_mode`/`web_password`/`external_api_token`/`_require_auth`/`cow_auth_token` 残留：部署/文档鉴权说明已清；归档/历史 release 与非鉴权 “legacy” 词保留；handler 级 `ctx is None` 余量见 4.7–4.12
- [x] 5.6 更新 `config-template.json`（已去 `web_password`/`external_api_token`）与部署脚本（`scripts/run.ps1`、`webhelp/assets/deploy/`、`docker/docker-compose.yml`、`webhelp/includes/config.php`、根 `run.sh`）为 `identity_mode=database`，移除 `WEB_PASSWORD`
- [x] 5.7 更新文档（上游文档登记 `merge-docs`）：`docs/{,zh,ja}/channels/web.mdx`、`docs/{,zh}/guide/quick-start.mdx`、`docs/{,zh}/guide/manual-install.mdx` 改为数据库账号登录 + Bearer `cow_session`，无 `web_password`
- [x] 5.8 运行 `openspec validate retire-legacy-identity-mode --strict`，确认规范 delta 与实际实现一致

## 6. 阶段 5：门禁与验收

- [x] 6.1 跑 `.venv/bin/python scripts/check_change_deltas.py retire-legacy-identity-mode` 通过（deltas 一致 + 冲突基线覆盖）
- [x] 6.2 身份/legacy 退役相关回归通过（bootstrap/service-account/platform-file/channels/identity/upstream-seams/route/不复活等）；全仓余下失败为环境依赖（缺 browser / pypdf），与本 change 无关
- [ ] 6.3 上游合并后回归：`tests/test_upstream_core_seams.py`、`tests/test_route_registry.py`、`tests/test_fork_fragments.cjs`、`tests/test_conversation_schema_seam.py`、`scripts/sync_report.py`、`scripts/check-route-coverage.py` 全部通过；上游新增路由均已在权威清单与策略表登记；`DELIBERATE_REMOVALS` 与基线 `keep-deletion` 行仍镜像一致且本 change 未新增误用
- [x] 6.4 安全验收：无凭据/旧 HMAC 拒绝；平台文件跨租户不可见；无身份 fail-closed；服务账号密钥生命周期测试通过
- [x] 6.5 执行隔离 fail-closed：无身份工具拒绝 + 安全事件告警；模型目录空集
- [x] 6.6 恢复演练：`tests/test_migration_recovery_acceptance.py` 通过；显式 legacy 启动拒绝，不开放共享密码
- [ ] 6.7 归档 change 并复核主规范无遗留 legacy 强制条款
