# Acceptance Table — `complete-enterprise-identity-access-control`

Reference for Tasks 1.3 / 6.1 / 6.5. Every row maps a spec requirement + scenario to the implementation slice, test evidence, and current status. Cross-referenced evidence is reused (a single test may satisfy multiple scenarios).

**Tests (2026-09-08, Python 3.14.3):**
Backend combined `pytest` on the change's slice files (incl. `test_identity_concurrency_acceptance.py` + migration/closure/owner slices) → **281 passed** (1 pre-existing deprecation warning: `setDaemon` in `channel/chat_channel.py`).
`node --test tests/test_identity_admin_frontend.cjs` → **9 passed**.
`node --check channel/web/static/js/console.js` → SYNTAX OK (verify on each change).

**Closure / owner evidence update (Task 6.4, 2026-09-08):**
Closure & owner/cold-start slice (`test_consumer_closure_acceptance.py`, `test_web_consumer_closure.py`, `test_channel_closure_startup.py`, `test_chat_identity_context.py`, `test_conversation_store_owner.py`, `test_identity_self_context.py`, `test_http_policy.py`, `test_revocation_takes_effect.py`, `test_web_navigation_mode.py`) → **62 passed** (1 pre-existing deprecation warning: `setDaemon` in `channel/chat_channel.py`).
See `route-inventory.md` (Task 1.2) and the closure rows below (C-7 / D-11 / D-12 / D-13).

| # | Spec | Requirement / Scenario | Implementation slice | Evidence (test / file) | Status |
| --- | --- | --- | --- | --- | --- |
| A-1 | enterprise-access-enforcement | 真实服务与测试共享完整路由策略 (`/auth/context` etc.) | `auth/http_policy.py` + `build_web_app()` installs `enforce_http_policy` | `test_http_policy.py` (unknown 404, unregistered method 405, closed 503, tenant_admin cannot override) | ✅ |
| A-2 | enterprise-access-enforcement | 未登记方法 & 关闭消费者 503 | `ROUTE_POLICY` completed + processor rejects before handler | `test_http_policy.py`, `test_consumer_closure_acceptance.py` | ✅ |
| A-3 | enterprise-access-enforcement | 旧凭据不给 database 资格，仅 Cookie/Bearer | `auth/credential` selection in db mode | `test_identity_credential.py` (legacy rejected, mixed 400) | ✅ |
| A-4 | enterprise-access-enforcement | 混用有效 Cookie + 无效 Bearer → 400 mixed_credentials | credential selection | `test_identity_credential.py::test_mixed_cookie_and_bearer`, `test_different_values_is_mixed` | ✅ |
| A-5 | enterprise-access-enforcement | 同值重复凭据只认证一次按 Cookie 保护 | credential selection | `test_identity_credential.py` | ✅ |
| A-6 | enterprise-access-enforcement | Web 登录只签发 Cookie | `DbAuthLoginHandler` | `test_identity_web_handlers.py` | ✅ |
| A-7 | enterprise-access-enforcement | 显式 legacy 保持兼容且在 db 拒绝 | credential + http_policy legacy branch | `test_http_policy.py::test_legacy_mode_not_gated_by_database_closure`, `test_identity_self_context.py::test_self_context_rejects_legacy` | ✅ |
| A-8 | enterprise-access-enforcement | 强制改密平台管理员受限 + 来源检查不可绕过 | shared guard + origin/CSRF check in handlers | `test_identity_auth_security.py` (cross-origin, missing source, bearer bypass) | ✅ |
| A-9 | enterprise-access-enforcement | 请求身份域统一解析 + 即时撤权 | `auth/runtime.py::resolve_context`, `revalidate_context` | `test_identity_runtime.py`, `test_revocation_takes_effect.py` (session/role/account/member/tenant revocation) | ✅ |
| A-10 | enterprise-access-enforcement | 临时密码期限 + 受限会话不超期 | `temp_password_expires_at` in bootstrap/login | `test_temp_password_expiry.py` (real bootstrap records expiry, weak skips, expired rejected) | ✅ |
| A-11 | enterprise-access-enforcement | 有界登录限流 (429) + 应用生命周期共享 | `auth/ratelimit.py` + `auth_handlers.py` | `test_ratelimit.py` (11), `test_identity_auth_security.py` (429 with retry-after, source signature) | ✅ |
| A-12 | enterprise-access-enforcement | 租户资料限 `tenant.info.read` + 白名单 | `TenantInfoHandler` field whitelist | `test_tenant_read_scoping.py`, `test_identity_audit.py::test_removes_secret_fields` | ✅ |
| A-13 | enterprise-access-enforcement | 审计限平台/本租户管理员，先过滤再分页 | `IdentityAuditHandler` | `test_identity_audit.py` (tenant-scoped, never secrets) | ✅ |
| A-14 | enterprise-access-enforcement | 身份写/登录短事务：锁外算密码、同连接重核、版本条件、审计同事务 | `auth/service.py` writes (`_tx` BEGIN IMMEDIATE) | `test_identity_service_writes.py` (expected_version guard), `test_revocation_takes_effect.py::test_audit_write_failure_rolls_back_creation`, `test_identity_self_context.py` (transaction atomic), `test_identity_concurrency_acceptance.py` (real concurrency) | ✅ |
| A-15 | enterprise-access-enforcement | 拒绝事件审计不改变拒绝结果 | audit append-only | `test_identity_audit.py::test_denied_event_structure`, `test_append_only` | ✅ |
| B-1 | account-administration | 平台/租户分域 | handlers routing + policy classification | `test_identity_web_handlers.py`, `test_http_policy.py::test_platform_account_routes_classified_platform` | ✅ |
| B-2 | account-administration | 敏感变更复核当前密码 + 版本保护 | `recent_password` + `expected_version` | `test_platform_user_admin.py::test_global_disable_requires_tenant_admin_continuity`, `test_identity_web_handlers.py::test_platform_user_patch_last_admin_rejected` | ✅ |
| B-3 | account-administration | 最后管理员连续性 + 并发只允许一个 | admin continuity check | `test_identity_service_writes.py::test_cannot_remove_last_tenant_admin`, `test_add_second_admin_then_remove_first_ok`; `test_platform_user_admin.py`; `test_identity_concurrency_acceptance.py::test_only_one_of_concurrent_double_demote_commits` (real concurrency) | ✅ |
| B-4 | account-administration | 账户重置立即撤销全部会话 + 临时密码一次性 | reset session revoke | `test_identity_self_context.py::test_change_password_revokes_all_old_sessions`; `test_identity_web_handlers.py` | ✅ |
| B-5 | account-administration | 管理员重置自身被拒绝 | reset self-actor guard | `test_identity_web_handlers.py` | ✅ |
| B-6 | account-administration | 成员复用 q/department/page + status/role 筛选 + role_codes | member list query | `test_identity_service_writes.py`, `test_identity_web_handlers.py` | ✅ |
| B-7 | account-administration | 绑定保留全局密码/其他租户关系 | bind-existing | `test_identity_service_writes.py::test_bind_existing_preserves_global_password` | ✅ |
| B-8 | account-administration | 并发新建唯一约束不自动转绑定 | unique constraint | `test_identity_service_writes.py` | ✅ |
| B-9 | account-administration | 成员更新省略 roles 保留 / 显式空数组 400 | member update semantics | `test_identity_service_writes.py` | ✅ |
| B-10 | account-administration | 租户名称/启停/管理员配置受控闭环 + 版本 | tenant admin config | `test_identity_web_handlers.py`; `test_platform_user_admin.py` | ✅ |
| B-11 | account-administration | register 精确解析 admin-username | `cli/commands/management.py` | `test_identity_web_handlers.py` | ✅ |
| B-12 | account-administration | 升级保留身份 & 旧 NULL 期限 bootstrap 经兼容改密 | migration + compat path | `test_temp_password_expiry.py`, `test_identity_migration_drill.py` (completed-admin present, weak bootstrap completed, NULL-expiry compat) | ✅ |
| B-13 | account-administration | 迁移/备份/中断重试/恢复不破坏身份；未满足即停；不切 legacy/不删迁移标识 | `auth/store.py` migrations, `app.py::_guard_identity_mode_consistency` | `test_migration_recovery_acceptance.py` (10), `test_identity_migration_drill.py` (7: complete gate, compat, backup/interrupt/retry, restore, legacy refusal) | ✅ |
| C-1 | business-permission-catalog | 九项权限元数据 (id/group/label/desc/scope/assignable) | `auth/policy.py` catalog | `test_identity_policy.py::test_catalog_has_seven_read_permissions` | ✅ |
| C-2 | business-permission-catalog | 未知/通配符/平台/身份管理权限拒绝 | catalog validation | `test_identity_policy.py::test_unknown_and_admin_permissions_rejected` | ✅ |
| C-3 | business-permission-catalog | 内置角色显式默认集合 + 升级不自动扩权 | explicit default set | `test_identity_policy.py::test_builtin_roles_defined`, `test_member_default_permissions` | ✅ |
| C-4 | business-permission-catalog | 权限并集 + 资源范围约束 | `permissions_for` | `test_identity_policy.py::test_permissions_for_roles_merges_union`, `test_tenant_read_scoping.py` | ✅ |
| C-5 | business-permission-catalog | `/auth/context` 只返回当前租户本人能力 | `DbAuthContextHandler` | `test_identity_web_handlers.py`, `test_identity_scope_gate.py` | ✅ |
| C-6 | business-permission-catalog | 零权限成员可读 / 缺选择 400 / 无成员 403 / 故障 503 | context handler | `test_identity_self_context.py`, `test_identity_scope_gate.py` | ✅ |
| C-7 | business-permission-catalog | 本期不开放延期消费者 | closed policy + consumers closed | `test_consumer_closure_acceptance.py`, `test_web_consumer_closure.py` | ✅ |
| D-1 | identity-management-workbench | 四管理菜单能力驱动 + 只读无写按钮 | frontend `console.js` + `/auth/context` | `test_identity_admin_frontend.cjs`, documented in 4.x specs | ✅ (backend) |
| D-2 | identity-management-workbench | database 首页不默认聊天 + 明确不可用原因 | frontend home + `/auth/context` | `test_web_consumer_closure.py` | ✅ |
| D-3 | identity-management-workbench | 用户页区分平台账号页签/当前租户成员 + 完整生命周期 | frontend `console.js` user page | `test_identity_admin_frontend.cjs` | ✅ (backend) |
| D-4 | identity-management-workbench | >100 成员真实分页 | member list page_size | `test_identity_web_handlers.py` | ✅ |
| D-5 | identity-management-workbench | 成员编辑回显 role_codes + 409/403 处理 | frontend member edit | `test_identity_admin_frontend.cjs` | ✅ (backend) |
| D-6 | identity-management-workbench | 租户页名称/启停/管理员配置 + shared_root 不暴露 | frontend tenant page | `test_identity_admin_frontend.cjs` | ✅ (backend) |
| D-7 | identity-management-workbench | 角色页分组权限/关联成员/复制 | frontend role page | `test_identity_admin_frontend.cjs` | ✅ (backend) |
| D-8 | identity-management-workbench | 组织树 + 部门移动 + 成员筛选 | frontend org page | `test_identity_admin_frontend.cjs` | ✅ (backend) |
| D-9 | identity-management-workbench | 审计筛选/分页 + 权限感知菜单 | frontend audit page | `test_identity_admin_frontend.cjs` | ✅ (backend) |
| D-10 | identity-management-workbench | 租户切换整页刷新隔离旧状态 | frontend switch logic | documented in 3.x–5.x, browser verify (6.2) A→B→A / tab-independent / old-write-in-original-tenant | ✅ (browser 6.2) |
| D-11 | identity-management-workbench | database 冷启动只启 web console，外部通道/调度/MCP warmup 关闭 | `app.py::_resolve_startup_channels`, `run()` | `test_channel_closure_startup.py` (opens only web, drops external, skips scheduler/MCP warmup, terminal closed) | ✅ |
| D-12 | identity-management-workbench | Bridge 构造不初始化运行消费者 (registry/router/initializer/scheduler) | `bridge/agent_bridge.py::__init__` early-return in database | `test_consumer_closure_acceptance.py::BridgeClosureTests` | ✅ |
| D-13 | identity-management-workbench | legacy 明确部署兼容 / database 不失败回退 | `_is_database_mode` default to legacy; `_guard_identity_mode_consistency` refuses legacy over migrated db | `test_consumer_closure_acceptance.py::RealConfigHelperTests`, `test_http_policy.py::test_legacy_mode_not_gated_by_database_closure`; `test_identity_self_context.py` | ✅ |
| D-14 | identity-management-workbench | 管理/目录维护不开放聊天、文件、调度、外部通道或 Desktop 企业运行 | policy `closed` + frontend `can_chat=False` + Bridge closure + startup resolution | `test_web_consumer_closure.py`, `test_consumer_closure_acceptance.py`, `test_channel_closure_startup.py`, `_workbench_chat_readiness` → `can_chat=False` | ✅ |

## PRD gap & cross-change ownership notes

- PRD原文 (`doc/优化规划/PRD/PRD-00-总目录.md`, PRD-01~12 v1.2) identified as **missing** in the current tree; the mapping to G01-G09 comes from `docs/design/user-role-permission-gap-and-plan.md`. Exact PRD↔requirement re-mapping deferred until PRD restored; no invented IDs/numbers used.
- Cross-change ownership:
  - `add-tenant-identity-access-management` — owns identity/session/transaction/resource-isolation primitives reused here.
  - `extend-sidebar-account-actions` — owns `/auth/me`, self-password change, refresh-based tenant switch (reused here; fully done 17/17).
  - `add-sidebar-account-menu` — owns account status/menu state (reused).
  - `add-workbench-appearance-preferences`, `add-workbench-todos` — own theme/language & todos (not part of this change).
  - `complete-enterprise-identity-access-control` — THIS change owns authorization enforcement, account administration, permission catalog, identity workbench.
- Explicit deferrals (NOT this change): resource grants/model selection, chat execution, files/preview, scheduler delegation, OpenAI API, external channels, SSO, Desktop enterprise login/transport, new CLI account management. These stay closed / non-goals.
- Deployment boundary: single-process web is the supported baseline for the shared login rate-limit state; multi-worker/multi-instance deployments that would bypass the in-process counter are explicitly rejected (`test_ratelimit.py`, `auth/ratelimit.py` BOUNDED + process-scoped). No Redis or cross-process counter was introduced.
- Migration/recovery contract: only needed fields/indexes migrate (`auth/store.py` `schema_migrations`); deferred consumer tables are NOT pre-created; upgrade stops if no completed-password platform admin remains; legacy is never auto-selected and the migration marker is never deleted to bypass protection (`test_migration_recovery_acceptance.py`, `test_identity_migration_drill.py`, `app.py::_guard_identity_mode_consistency`).
