## 1. 权限策略与解析

- [x] 1.1 `auth/policy.py`：把 `MEMBER_DEFAULT_PERMISSIONS` 改为显式 18 项「使用型 + 可创建自有资源」集合（`tenant.info.read, agent.read, agent.use, agent.edit, history.read, knowledge.read, memory.read, todo.read, todo.write, skill.read, skill.use, skill.edit, tool.read, tool.execute, tool.configure, model.read, model.use, chat.use`）
- [x] 1.2 `auth/policy.py`：把 `TENANT_ADMIN_DEFAULT_PERMISSIONS` 改为显式列出全部已登记目录项（含 `knowledge.write`），保持 `member` 超集；不复用 `PERMISSION_CATALOG` 动态展开
- [x] 1.3 `auth/policy.py`：`permissions_for_roles()` 改为优先取 `role_permissions` 中的持久化集合（含空集），仅在编码未持久化时对内置编码回落默认集合；新增 `TENANT_ADMIN_MINIMUM_PERMISSIONS`（`tenant.info.read`、`tenant.members.read`、`tenant.org.read`）

## 2. 身份服务

- [x] 2.1 `auth/service.py`：`_permissions_for_membership()` 不再排除内置编码，对全部角色读取 `permissions_json` 填入映射后交给 `permissions_for_roles()`
- [x] 2.2 `auth/service.py`：建租户种子改用 `default_permissions_for(TENANT_ADMIN_CODE)` / `default_permissions_for(MEMBER_CODE)`，删除硬编码七项与 `list(PERMISSION_CATALOG)`
- [x] 2.3 `auth/service.py`：`update_role()` 允许内置角色编辑（名称/权限/资源授权/默认模型），移除 `builtin` 硬拒绝；保存 `tenant_admin` 时校验仍包含最小身份读取权限，否则 409 整笔拒绝
- [x] 2.4 `auth/service.py`：`delete_role()` 继续拒绝内置角色；`create_role()` 继续拒绝内置编码重名；确认审计事件与版本冲突语义不变

## 3. 迁移

- [x] 3.1 `auth/store.py`：新增版本化迁移，将 `roles` 中 `builtin=1 AND code IN ('member','tenant_admin')` 的行回填为对应显式默认集合并 `version = version + 1`
- [x] 3.2 确认迁移幂等、按 schema 版本仅执行一次、不触碰自定义角色与其他租户

## 4. 前端

- [x] 4.1 `channel/web/static/js/identity-admin.js`：`loadRolesView()` 为内置角色渲染「编辑」入口（保留编辑/复制/查看成员，隐藏删除）
- [x] 4.2 如需要，补充/复用 i18n 文案说明内置角色可编辑但不可删除，并同步 `tests/fixtures/console_i18n_snapshot.json`（复用既有 `admin_*` 文案，未新增键，快照不变）

## 5. 测试与校验

- [x] 5.1 更新断言旧默认集合/内置只读的既有用例（`tests/test_identity_policy.py`、`tests/test_identity_web_handlers.py`、`tests/test_identity_service.py`、`tests/test_rbac_tenant_constraints.py` 等）
- [x] 5.2 新增后端用例：成员有效权限含 `chat.use`/`agent.use`；内置角色编辑保存后成员权限即时变化；`tenant_admin` 最小读取守卫拒绝；删除内置角色仍拒绝；迁移回填正确且不越租户
- [x] 5.3 新增/更新前端 `.cjs` 用例：内置角色出现编辑入口、不出现删除入口
- [x] 5.4 运行相关 pytest 与 `.cjs`、`node --check` 并确认通过
- [x] 5.5 `openspec validate make-builtin-roles-editable --strict` 通过
