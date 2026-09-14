## Why

平台管理员目前只能停用租户，无法把已废弃或测试用的租户从日常视图中移除，租户列表会长期堆积；但直接物理删除又会破坏身份数据、跨表引用与审计链。需要一个可逆的「删除」手段：把不再使用的租户收起，同时完整保留其数据以便恢复。

## What Changes

- 在「系统设置 → 租户」列表行操作区新增「删除」按钮，对租户执行**归档**（软删除）。
- 归档 = 置 `tenants.archived_at` 并同时置 `active=false`；归档租户按停用语义被登录租户列表、租户选择、租户作用域业务请求与私有数据目录拒绝，并从个人可用租户列表移除。
- 归档保留全部身份数据（成员、角色、部门、成员角色、资源授权、渠道实例、外部身份绑定、审计）与租户空间目录，**MUST NOT 物理删除**。
- 提供恢复操作：清空 `archived_at`、置 `active=true`，复用「恢复停用租户需有效 `tenant_admin`」的连续性守卫。
- 归档守卫：有效平台管理员 + 近期密码校验 + `expected_version` 乐观锁（密码即确认强度，不要求输入租户编码）。
- 对象保护：部署默认租户（`code='default'`）与操作者当前请求上下文所属租户 MUST NOT 归档。
- 列表新增状态筛选（未归档 / 启用 / 停用 / 已归档），默认只返回未归档租户；已归档行显示归档标记与「恢复」入口，不显示编辑/删除。
- 归档与恢复各写一条同事务审计事件，并各只递增一次租户版本。
- 修订 `tenant-management` 中「MUST NOT 归档租户」的既有口径。

## Capabilities

### New Capabilities
<!-- 无新增能力 -->

### Modified Capabilities
- `tenant-management`: 允许平台管理员归档/恢复租户；定义归档的软删除语义、守卫、对象保护、列表状态筛选、审计与版本；移除既有「MUST NOT 归档租户」约束。

## Impact

- 后端：`auth/store.py`（新增 migration 12：`tenants.archived_at`）、`auth/service.py`（`archive_tenant` / `restore_tenant`、`list_tenants` 状态筛选、`set_tenant_status` 与 `set_tenant_profile` 拒绝已归档租户）、`channel/web/admin_handlers.py`（`PlatformTenantHandler` 新增 `DELETE` 与 `operation='restore'`）、`channel/web/route_registry.py`（同一路由项新增 `DELETE`）。
- 前端：`channel/web/static/js/identity-admin.js`（行内删除/恢复按钮、确认弹窗、状态筛选）、`channel/web/static/js/i18n/identity-admin.js`（zh / zh-TW / en 文案）。
- 测试：新增 `tests/test_tenant_archive.py`；扩展前端 `.cjs` 断言；更新 `tests/fixtures/console_i18n_snapshot.json` 与 `scripts/route-baseline.txt`。
- 数据：仅新增可空列，既有行 `archived_at` 为 `NULL`（未归档）；无需回填，回滚只需移除代码，列本身无副作用。
- 不改动：租户 `code` / 稳定 ID、租户空间目录位置、成员/角色/授权授权规则、租户编辑器五标签结构、其它能力规范。
