## 1. 数据模型与迁移

- [x] 1.1 `auth/store.py` 新增 `_migration_12`：`ALTER TABLE tenants ADD COLUMN archived_at INTEGER`（可空），并追加到 `_migrations`
- [x] 1.2 确认既有库升级后 `archived_at` 全为 `NULL`（未归档），新建库与升级库 schema 一致

## 2. 服务层

- [x] 2.1 `auth/service.py` 新增 `archive_tenant(*, actor_user_id, tenant_id, expected_version, recent_password, current_tenant_id=None)`：平台管理员 + 近期密码 + 版本校验；拒绝 `code='default'` 与操作者当前租户；同事务置 `archived_at`/`active=0`、跑既有 `_check_all_affected_active_tenant_continuity`、`version+1` 并写 `tenant.archive` 审计
- [x] 2.2 `auth/service.py` 新增 `restore_tenant(...)`：清 `archived_at`、置 `active=1`、复用管理员连续性守卫、`version+1`、写 `tenant.restore` 审计
- [x] 2.3 `auth/service.py` 的 `set_tenant_status` 与 `set_tenant_profile` 对已归档租户返回 409/明确的 `archived` 错误，要求先恢复
- [x] 2.4 `auth/service.py` 的 `list_tenants(q, status=None)` 支持 `active`/`inactive`/`archived`/`all` 筛选，默认（None）排除已归档
- [x] 2.5 租户投影（`_tenant_public` / `_tenant_platform_public`）带上 `archived` 标记，供前端渲染归档行

## 3. HTTP 与路由

- [x] 3.1 `channel/web/admin_handlers.py` 的 `PlatformTenantHandler` 新增 `DELETE(tenant_id)`：`_require_management_write()` + 平台管理员 + `recent_password`/`expected_version`，调用 `archive_tenant`
- [x] 3.2 `PlatformTenantHandler.POST` 增加 `operation='restore'` 分派（当前版本 + 近期密码），调用 `restore_tenant`
- [x] 3.3 `GET /api/platform/tenants` 支持 `status` 查询参数并透传 `list_tenants`
- [x] 3.4 `channel/web/route_registry.py` 在 `/api/platform/tenants/([^/]+)` 增加 `DELETE`；更新 `scripts/route-baseline.txt`

## 4. 前端

- [x] 4.1 `identity-admin.js` 的 `loadTenantView` 渲染行内「删除」按钮（仅未归档、非默认租户、非当前租户），已归档行渲染归档标记与「恢复」按钮
- [x] 4.2 复用 `openAdminModal` 实现归档确认弹窗：只收集 `recent_password` 密码（必填），提交前不要求输入租户编码
- [x] 4.3 实现 `archiveTenant(id)` / `restoreTenant(id)` 与 `adminRowAction('tenant','delete'|'restore', id)` 分派；409 重新加载、403/404/密码错误可重试并保留输入
- [x] 4.4 列表新增状态筛选（未归档/启用/停用/已归档），默认未归档；筛选与搜索联动重载
- [x] 4.5 `channel/web/static/js/i18n/identity-admin.js` 增加 zh / zh-TW / en 文案（删除、已归档、恢复、确认提示、错误）

## 5. 测试与校验

- [x] 5.1 新增 `tests/test_tenant_archive.py`：归档保留数据/退出服务、缺近期密码/密码错误、默认租户与当前租户保护、版本冲突、归属连续性拒绝、审计同事务、已归档不可编辑/启用、恢复需管理员、恢复后可选择、code 仍占用、列表筛选
- [x] 5.2 扩展前端 `.cjs` 断言：删除/恢复按钮渲染与隐藏、状态筛选、确认弹窗字段（`tests/test_tenant_archive_frontend.cjs`）
- [x] 5.3 更新 `tests/fixtures/console_i18n_snapshot.json`（新增文案）
- [x] 5.4 运行相关 pytest 与 `.cjs` 测试并确认通过
- [x] 5.5 `openspec validate add-tenant-archive --strict` 通过

> 5.4 说明：本 change 的 `tests/test_tenant_archive.py`（20 passed）、`tests/test_tenant_archive_frontend.cjs`（6 passed）、`tests/test_tenant_create_frontend.cjs`、`tests/test_tenant_tabbed_editor_frontend.cjs`、`tests/test_tenant_admin_account_picker.cjs`、`tests/test_route_registry.py` 均通过。
> 工作区中 `tests/test_sidebar_account_frontend.cjs`（5 条登录/偏好用例）与 `tests/test_console_i18n_parity.cjs`（`config_password_*` / `tasks_unavailable_desc` 文案）存在与本 change 无关的既有失败，来自并行进行中的账号/头像变更，未在本 change 内处理。
