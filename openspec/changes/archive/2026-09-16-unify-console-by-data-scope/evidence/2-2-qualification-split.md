# 2.2 页面资格 / 本人对象操作 / 公共配置管理资格

本文件记录 `tasks.md` 2.2：三种「能不能」必须分开，且组织与权限接口不因控制台开放而开放。

## 1. 三种资格的区分

| 资格 | 载体 | 判定 | 不授予什么 |
|---|---|---|---|
| 正式页面资格 | `role_resource_grants` 的 `menu` grant（`nav:admin.*` / `nav:workbench.*`） | 菜单投影 `console_pages` | 不授予任何对象写入；菜单投影不替代接口授权（见 2.5） |
| 本人对象操作 | `agent_bindings.private_owner_user_id` / `tenant_channel_instances.owner_user_id` | `auth/object_scope.py` 的 owner 分支 | 不授予他人或共享对象（2.1 证据） |
| 公共配置管理资格 | 管理角色（`tenant_admin` / 平台） | `allows_public_configuration()` | 成员持有的公共 `edit` grant 不替代 |

关键实现：`auth/object_scope.py:183`

```
def allows_public_configuration(self) -> bool:
    return self.is_admin
```

——**只有管理资格**，刻意不读任何功能权限或资源 grant。因此一个自定义角色即使被授予
`skill.edit` / `tool.configure` 以及某个公共 skill id 的 `edit` grant，仍拿不到公共维护资格。

## 2. 同一规则作用于共享智能体的配置写入

`_require_agent_management_scope`（`channel/web/web_channel.py:616`）把「成员持有 per-resource
`skill.edit`」与「维护租户共享定义」分开：命名租户**共享**智能体的配置写入需要管理资格，只有本人私有
智能体才由 owner 通过。这条闸门与智能体列表读共用同一谓词，因此「列表能看到的」与「写得进的」不会
互相漂移。

## 3. 组织与权限接口继续拒绝普通用户

- 路由策略：`/api/tenant/roles`、`/api/tenant/members`、`/api/platform/*` 的 `POST/PUT/DELETE` 走
  `P("tenant")` / `P("platform")` 管理策略（`channel/web/route_registry.py:141-153`），不经菜单投影。
- 证据（HTTP 级）：`tests/test_scope_consistency_acceptance.py:264`
  `test_an_open_console_does_not_open_role_management` 以普通成员会话 `POST /api/tenant/roles`，
  断言 401/403 且租户角色列表未新增该 code。
- 同一文件 `:275` `test_a_public_edit_grant_is_not_management_qualification` 先给成员角色加上
  `agent:shared-agent` 的 `edit` grant 与 `skill:public-skill` 的 `edit` grant，再断言：
  `allows_public_configuration()` 为 `False`，而 `check_resource_action(..., "edit")` 为 `True`
  （grant 真实生效，只是不构成管理资格）；租户管理员上下文则为 `True`。

## 4. 证据

```
.venv/bin/python -m pytest tests/test_scope_consistency_acceptance.py -q
→ 20 passed
.venv/bin/python -m pytest tests/test_object_scope.py tests/test_builtin_role_editing.py \
  tests/test_http_policy.py tests/test_rbac_tenant_constraints.py -q
→ 全通过（见 2.5 汇总）
```

## 5. 阶段边界

公共配置的**页面**（技能/工具/模型共用页）在阶段 5；本阶段完成的是资格区分与「不替代」这条不变式，
以及组织/权限接口的既有拒绝行为在控制台开放后仍成立。

## 6. 补齐：组织与权限是资格面，不是权限面（读取也必须拒绝）

§3 记录的是**写**接口（POST/PUT/DELETE）的拒绝。收口 `unify-console-by-data-scope` 时发现
「继续拒绝普通用户」在**读**这条路径上并未成立，两处都按功能权限放行：

| 位置 | 修复前 | 修复后 |
|---|---|---|
| 投影 `_console_pages_projection` | `admin.members` / `admin.roles` / `admin.organization` 的 `available` 恒为 `True`，`read_allowed` 取功能权限 | `available` = 管理资格；`read_allowed` = 管理资格**且**功能权限；拒绝时 `reason="no_permission"` |
| 接口 `TenantMembersHandler.GET` / `TenantRolesHandler.GET` / `TenantDepartmentsHandler.GET` | 只要求 `tenant.members.read` / `tenant.org.read` | 追加 `_require_tenant_admin(ctx)` |
| 投影 `admin.tenants` | `available` 恒为 `True`（对租户管理员也成立） | `available` = 平台资格 |

依据：`specs/rbac-authorization/spec.md` 的 MODIFIED「页面能力与接口使用同一授权规则」——
「**WHEN** 普通成员持有组织或成员读取权限但不具备管理资格 **THEN** 控制台中的成员管理、角色权限、
组织架构及对应管理接口拒绝」。与 `openspec/specs/organization-management/spec.md`
（「读取 SHALL 要求当前有效成员具有 `<perm>`」）不冲突：该条给出的是**必要**条件，本节追加的是
资格这一附加必要条件。

投影那一半不是美观问题。前端 `_applySidebarPermissions` 的逐项闸门是 `available || read_allowed`
即显示，分组又按「是否还有未隐藏子项」收口（`console-information-architecture`：不展示空分组），
所以 `available=True` 会让成员既看到「组织与权限」分组与三项入口，又点进一个正文拒绝的页面且
`reason` 为空——既违反上面的 scenario，也让「空分组收口」失效。

### 验证（真实服务 `web` 渠道，端口 9899）

`RC001`（成员，角色 `member`）与 `test15`（租户管理员）实际登录后取 `/auth/context`：

```
RC001   admin.members/roles/organization  avail=False read=False reason='no_permission'
        admin.channels                    avail=True  read=True  scope='self'
        admin.agents/memory/skills        avail=True  read=True  scope='agent'
test15  admin.members/roles/organization  avail=True  read=True
        admin.tenants                     avail=False read=False reason='no_permission'
```

对应管理接口（带 `X-Tenant-ID`）：

```
/api/tenant/members      RC001 -> 403   test15 -> 200
/api/tenant/roles        RC001 -> 403   test15 -> 200
/api/tenant/departments  RC001 -> 403   test15 -> 200
```

回归测试（新增，两者都含「持有读取权限的普通成员」这一对照组）：

```
.venv/bin/python -m pytest tests/test_menu_grant_enforcement.py -q
→ 9 passed（含 test_the_read_permission_alone_does_not_open_the_org_pages、
             test_the_platform_accounts_page_is_not_offered_to_a_tenant_admin）
.venv/bin/python -m pytest tests/test_identity_web_handlers.py -q
→ 全通过（含 test_holding_the_read_permission_does_not_open_the_member_surface、
             test_the_tenant_admin_keeps_the_same_surface）
```

同时把 `tests/test_menu_grant_enforcement.py::test_menu_grants_also_gate_admin_pages` 的旧断言
（非管理员「组织只读」角色 `admin.organization.read_allowed` 为 `True`）改为新契约，它编码的是
本 change 之前的语义。
