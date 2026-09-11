## 1. 服务层：租户创建与管理员创建解耦

- [x] 1.1 将 `auth/service.py` 的 `create_tenant()` 的 `admin_username` / `admin_display` / `admin_password` 改为可选参数（默认 `None` / `""` / `None`），保留 `shared_root` 可选语义与文档字符串更新。
- [x] 1.2 在事务外按 `admin_username` 与 `admin_password` 是否同时非空判定「显式指定初始管理员」；仅在显式指定时执行弱密码校验（`_COMMON_PASSWORDS` / `MIN_PASSWORD_LENGTH`）与密码哈希，未指定时跳过。
- [x] 1.3 在 `BEGIN IMMEDIATE` 事务内条件化写入：无初始管理员时不插入 `users` / `memberships` / `membership_roles` 行；`tenants`、两个内置 `roles`、`departments.__root__` 与 `audit_events` 保持不变。
- [x] 1.4 确认 `_derive_tenant_shared_root` 与 `_assert_new_tenant_root_clear` 仍在写入前执行，且新路径不改变跨租户目录包含拒绝（`shared_root_conflict` / `config_error`）。
- [x] 1.5 确认返回值仍为 `{id, code, name, active, version}`，不含账号字段与临时密码。

## 2. Handler：请求与响应字段收敛

- [x] 2.1 修改 `channel/web/admin_handlers.py` 的 `PlatformTenantsHandler.POST`，只读取 `code` / `name` / `recent_password`，不再读取或透传 `admin_*`。
- [x] 2.2 确认 `recent_password` 仍通过 `_recent_password(ctx)` 传递，`_require_platform_admin` 与 `auth/http_policy.py` 的 `platform` 策略未被改动。
- [x] 2.3 确认旧前端残留的 `admin_*` 字段被忽略（不报 400），且响应不含 `admin_username` / `admin_password` / 临时密码。

## 3. 前端：表单精简与创建后自动引导

- [x] 3.1 修改 `channel/web/static/js/identity-admin.js` 的 `openTenantCreate()`，字段列表只保留 `code` / `name` / `recent_password`。
- [x] 3.2 在 `openAdminModal()` 中支持可选 `afterSuccess` 回调，并在 `submitAdminModal()` 内于 `closeAdminModalNoPrompt()` **之后**、`status()` 提示之前调用；`afterSuccess` 抛错不得影响成功提示。
- [x] 3.3 在 `openTenantCreate()` 的提交逻辑中捕获新建租户 id，并在 `afterSuccess` 中调用现有 `openTenantAdmin(id)`；`loadTenantView()` 仍先刷新列表，保证 `_tenantById[id]` 可解析。
- [x] 3.4 为 `openTenantAdmin()` 在「从创建流程跳入」时保持原有字段与提交行为（绑定已有有效账号、`admin_tenant_admin_edit` 标题、`recent_password` 必填），不新建对话框类型。
- [x] 3.5 保留 `console.js` 中 `admin_field_admin_*` 文案键（被 `openTenantAdmin` 与成员表单复用），不新增文案键。

## 4. 测试

- [x] 4.1 服务层新增用例：不传 `admin_*` 创建租户 → 成功、`active=true`、无 `users` / `memberships` / `membership_roles` 行、内置角色与组织根已建立。
- [x] 4.2 服务层新增用例：不传 `admin_*` 时 `recent_password` 缺失/错误仍被拒绝（保留二次授权）。
- [x] 4.3 服务层回归用例：显式传入 `admin_*` 时仍建立管理员、弱密码仍被拒、`code` 冲突仍返回 409、共享目录包含冲突仍被拒。
- [x] 4.4 `tests/test_identity_web_handlers.py` 新增/调整用例：`POST /api/platform/tenants` 请求体只含 `code` / `name` / `recent_password` 时创建成功且响应无临时密码；携带旧 `admin_*` 字段时同样被忽略。
- [x] 4.5 新增前端用例：创建租户表单不含 `admin_username` / `admin_password` 字段；创建成功后自动打开管理员配置对话框（断言回调在关闭后触发、接续对话框可见）。
- [x] 4.6 端到端串接用例：创建租户 → 绑定已有有效 User → 该 User 成为有效 `tenant_admin`；未绑定前停用再恢复该租户被拒绝并保持 `active=false`。

## 5. 文档与验收

- [x] 5.1 在 `openspec/changes/simplify-tenant-creation-form/` 补充测试与运行证据（命令、用例名、结果），不把接口就位当作已验收。
- [x] 5.2 记录范围缺口：系统尚无「新建平台账号」入口，新租户在绑定管理员前无法自行创建账号，需后续 change 处理。
- [x] 5.3 运行受影响测试集（identity 服务/子集 handler/租户包含/前端用例）并确认全绿，记录实际命令与输出。
- [x] 5.4 归档前执行 `openspec validate simplify-tenant-creation-form --strict` 与 `openspec status`，确认 delta 生效且任务全部勾选。
