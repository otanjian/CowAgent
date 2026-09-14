## Why

内置角色 `member`/`tenant_admin` 当前的权限集合既不合理也不一致：

- `member` 的有效权限被 `auth/policy.py` 固定为七项只读 + 待办（`MEMBER_DEFAULT_PERMISSIONS`），**不含 `chat.use`、`agent.use`、`model.use`**。普通成员因此连最基础的对话都发起不了，与《产品规划》3.1「用户可使用租户管理员分配的资源，工作台以对话为默认首页」直接矛盾。
- `tenant_admin` 存在「页面显示」与「实际生效」两套口径：建租户时 `roles.permissions_json` 写入的是整份目录（`list(PERMISSION_CATALOG)`），控制台据此渲染；而 `permissions_for_roles()` 对内置角色 **忽略持久化值**，只返回固定九项 `TENANT_ADMIN_DEFAULT_PERMISSIONS`。管理员在页面上看到的权限与实际判定不一致。
- 两个内置角色在后端 `update_role`/`delete_role` 被硬拒绝（`built-in role cannot be modified`），前端也不渲染「编辑」按钮，租户无法按自身需要调整。

结果：成员无法正常使用平台，管理员无法按 3.1 的职责范围调整内置角色，且内置角色的授权事实不可信。

## What Changes

- **调整内置默认集合（显式枚举，不随目录自动扩张）**：
  - `member` 改为「使用型 + 可创建自有资源」集合：`tenant.info.read, agent.read, agent.use, agent.edit, history.read, knowledge.read, memory.read, todo.read, todo.write, skill.read, skill.use, skill.edit, tool.read, tool.execute, tool.configure, model.read, model.use, chat.use`（对应 3.1 用户可对话/使用被授权资源，及 3.2 成员可在本租户创建并维护自有智能体/工具/技能）。
  - `tenant_admin` 改为当前已登记的全部目录项（含 `knowledge.write`），作为 `member` 集合的超集，匹配实现其 3.1.3 的 2.1～2.3 职责。
- **内置角色的有效权限以持久化集合为准**：`permissions_for_roles()` / `_permissions_for_membership()` 不再对内置角色短路到固定常量，而是读取该角色行的 `permissions_json`；未持久化时回落到显式默认集合。使控制台「显示」与「生效」同源。
- **允许编辑内置角色**（名称、功能权限、资源授权、默认模型），**仍禁止删除**、禁止修改编码与作用域；`tenant_admin` 的身份管理资格继续由**角色编码**决定，不因编辑权限而增删；并保护 `tenant_admin` 至少保留身份工作台所需的读取权限（`tenant.info.read`、`tenant.members.read`、`tenant.org.read`），避免在控制台内自锁。
- **版本化迁移回填**：对既有租户的内置角色行回填为新默认集合，`member` 获得新增的使用/创建权限，`tenant_admin` 对齐全目录。
- 前端角色页为内置角色渲染「编辑」入口（保留「查看成员」「复制」，不渲染「删除」）。

## Capabilities

### Modified Capabilities
- `business-permission-catalog`: 内置 `member`/`tenant_admin` 的显式默认集合调整；内置角色有效权限以持久化集合为准、显式默认仅作回落；新增权限仍不自动进入内置集合。
- `rbac-authorization`: 内置角色可编辑（名称/功能权限/资源授权/默认模型），仍不可删除、编码与作用域不可改；身份管理资格与管理员连续性保护保持不变，新增 `tenant_admin` 最小身份读取保护。
- `identity-management-workbench`: 角色页内置角色渲染编辑入口并可保存生效，删除仍不可用。

## Impact

- 后端：`auth/policy.py`（`MEMBER_DEFAULT_PERMISSIONS`/`TENANT_ADMIN_DEFAULT_PERMISSIONS` 显式枚举、`permissions_for_roles` 优先持久化值、新增内置角色最小读取保护常量）、`auth/service.py`（`_permissions_for_membership` 传入内置角色持久化集合、`update_role` 允许内置角色并加最小读取守卫、建租户种子改用显式默认集合）、`auth/store.py`（新增迁移回填内置角色权限）、`channel/web/admin_handlers.py`（沿用既有 `update_role` 调用，仅在必要处调整拒绝语义说明）。
- 前端：`channel/web/static/js/identity-admin.js`（内置角色渲染编辑入口；如需要补提示文案）、`channel/web/static/js/i18n/identity-admin.js` 及 i18n 快照。
- 测试：更新断言内置默认集合、内置只读/不可编辑的既有用例；新增编辑内置角色、回填迁移、最小读取保护、成员可对话等用例。
- 数据：新增一条版本化迁移，回填既有内置角色权限；不改动角色/成员/授权表结构，不越权到其他租户。
- 不改动：平台内置角色 `platform_admin`（独立作用域，不进入租户并集）；身份管理资格仍由角色编码派生；`knowledge.write` 仍可被自定义角色显式分配。
