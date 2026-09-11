本清单为待实施任务；勾选状态以实际代码与验证证据为准，不以文档存在代替实施完成。设计见 `design.md`，行为契约见 `specs/`。

## 1. 阶段一：数据模型与迁移（前置门槛）

- [x] 1.1 在 `auth/store.py` 新增 `_migration_6`：创建 `platform_roles` 与 `user_platform_roles` 表及 `platform_roles.code` 唯一索引。
- [x] 1.2 在迁移内幂等 seed `platform_admin` 平台内置角色（`builtin=1`、中文名「平台管理员」、显式权限集合），重复执行不产生重复行。
- [x] 1.3 在迁移内按 `users.is_platform_admin=1` 回填 `user_platform_roles`，并同步镜像列，保证绑定与镜像一致。
- [x] 1.4 注册 `_migration_6` 到 `_migrations`；并将死常量 `__schema_version__`（无任何引用）同步为当前迁移数量或直接删除，属纯清理、无功能影响。
- [x] 1.5 保持启动前置守卫语义（实例至少一个 `active=1 AND must_change_password=0` 的平台管理员）不因迁移放宽，并确认 `scripts/auth_preflight.py` 在迁移后行为正确。

## 2. 阶段二：策略与服务层唯一来源

- [x] 2.1 在 `auth/policy.py` 新增 `PLATFORM_ADMIN_CODE` 与显式 `PLATFORM_ADMIN_DEFAULT_PERMISSIONS`（仅作结构记录，本期不参与授权判定），并确认 `default_permissions_for`、`permissions_for_roles`、`is_admin_role` 不纳入平台角色。
- [x] 2.2 统一 `auth/service.py` 中 `is_platform_admin_user` 与 `is_platform_admin` 为单一实现，改为按平台角色绑定（并校验 `users.active=1`）判定。
- [x] 2.3 将 `authorization_mode`、`_require_platform_admin` 切换到新谓词，保持 `"all"`/`"role"` 与 403 语义不变。
- [x] 2.4 将**活代码** `_check_platform_admin_continuity` 改为按绑定统计有效（已完成改密）平台管理员，保持 `last_admin` 拒绝码与口径（排除 `must_change_password`）不变。
- [x] 2.5 处置死方法 `_count_valid_platform_admins`：核实其确无生产调用后删除，或（若保留）一并切换为按绑定统计并与连续性口径一致；记录处置结果。
- [x] 2.6 新增私有 `_set_platform_role(con, user_id, granted, actor_user_id)`：在同一写事务内增删绑定并同步 `users.is_platform_admin` 镜像，收敛为唯一写入路径。
- [x] 2.7 改造 `set_platform_user_status` 内部走 `_set_platform_role`，保持签名、返回字段、会话吊销、租户连续性行为与审计 `redacted_changes` 形状不变；移除直接写镜像列的 `UPDATE`。
- [x] 2.8 在 `bootstrap` 事务内将镜像列初始化为 `0`，再经 `_set_platform_role` 为初始平台管理员置 `1`；确认 `create_tenant` 初始管理员仍为普通租户管理员（镜像列写 `0`、无平台绑定）。

## 3. 阶段三：上下文解析与撤销生效

- [x] 3.1 将 `resolve_context` 两条分支与 `member_context` 的平台资格改为调用服务谓词，不再直接读取原始列。
- [x] 3.2 修正 `revalidate_context` 原样复制旧平台资格的问题（正确性/测试卫生修复，生产路径当前不经过它），改为重新派生。
- [x] 3.3 确认 `RequestContext.is_platform_admin` 字段与全部构造点签名不变，避免影响既有调用方与测试夹具。

## 4. 阶段四：HTTP、CLI 与前端兼容

- [x] 4.1 确认 `_require_platform_admin(ctx)` 与 14 条 `platform` 路由、`/config`、`/api/models`、branding 写入门禁行为不变（派生值读取）。
- [x] 4.2 保持 `PlatformUsersHandler.PATCH` 请求体与响应契约不变（仍接收/返回 `is_platform_admin`），并清理 `admin_handlers.py` 中审计作用域的死分支。
- [x] 4.3 将 `cli/commands/management.py` 的平台管理员校验改用服务谓词，避免绕过 `active` 复检。
- [x] 4.4 复核前端（`console.js`、`identity-admin.js`、`chat.html`）在契约不变前提下功能正确，不做必需改动。

## 5. 阶段五：测试与验证证据

- [x] 5.1 更新 `tests/test_platform_user_admin.py`：提升/降级/连续性经公共契约驱动，并新增绑定表断言。
- [x] 5.2 更新 `tests/test_identity_concurrency_acceptance.py` 的最后管理员并发不变量为按绑定校验。
- [x] 5.3 在 `tests/test_identity_migration_drill.py` 新增迁移后「绑定与镜像一致、存量平台管理员已回填」断言。
- [x] 5.4 新增回归：撤销平台资格后下一次请求 `authorization_mode != 'all'`（覆盖「资格撤销在后续操作生效」，含 `revalidate_context` 重新派生的正确性）。
- [x] 5.5 复核 `tests/test_identity_web_handlers.py`、`tests/test_http_policy.py`、`tests/test_agent_workbench.py`、`tests/test_tenant_default_agent.py` 等按既有契约继续通过，必要时补齐夹具。
- [x] 5.6 运行后端身份/授权测试与前端契约测试（含 `tests/test_sidebar_account_frontend.cjs`），保存实际结果作为证据。
- [x] 5.7 若按 tasks 2.5 删除了 `_count_valid_platform_admins`，确认无测试或生产代码引用后删除，并记录。

## 6. 阶段六：文档与变更核验

- [x] 6.1 修订 `docs/design/role-resource-authorization-plan.md` 中与平台角色化相冲突的措辞，记录新决策与理由。
- [x] 6.2 运行 `openspec validate platform-admin-role --strict` 并保存结果。
- [x] 6.3 确认本 change 未改动多租户业务隔离、私有 owner、业务会话协议、Desktop 与 Channel；无 feature flag 需求（绑定与镜像同事务一致）。
