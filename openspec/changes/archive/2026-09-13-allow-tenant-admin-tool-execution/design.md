## Context

工具执行的运行时门禁在 `agent/protocol/agent_stream.py::_resource_tool_denial`：解析 `RuntimeIdentity`，取 `_tool_resource_id(tool_name)`（MCP 工具为 `mcp:<connection>:<tool>`，其余为 `builtin:<tool>`），再调用 `IdentityService.check_resource_action(user, tenant, "tool", resource_id, "execute", permission="tool.execute")`。该方法要求功能权限与资源 grant 同时具备，平台管理员（`authorization_mode == "all"`）例外。

租户管理员的现有先例是智能体维度：`channel/web/web_channel.py::_tenant_admin_owns_agent` 以 `agent_bindings` 的租户归属为唯一判据，命中即免逐资源 grant。工具维度没有对应放行，导致 `tenant_admin` 内置角色（不携带资源 grant）无法执行任何工具。

同时，租户在自有智能体工作区配置的 MCP 工具只存在于运行时工具集与 `tenant_resource_grants` 之外；工具类别在 `_project_owned_ids` 中被视为平台全局，因此这些工具既不可指派也不可执行。

## Goals / Non-Goals

**Goals**
- 让租户管理员能执行本租户可用工具（平台已开放给该租户的内置工具 + 租户自有 MCP 工具），无需逐资源 grant。
- 保持普通成员逐资源授权、平台管理员 `all`、执行隔离、配额、Agent 工具范围与平台开放上限全部不变。

**Non-Goals**
- 不改工具的资源归属模型，不把 MCP 工具写入 `tenant_resource_grants`，不改角色/授权表结构。
- 不让豁免扩展为「租户管理员可执行任意工具」；平台未开放给该租户的 builtin 工具仍拒绝。
- 不改动会话权限模式（read-only / workspace-write / full-access）语义。

## Decisions

### 决策 1：豁免判定放在身份服务，运行时按需查询

新增 `IdentityService.tenant_admin_may_execute_tool(user_id, tenant_id, resource_id) -> bool`，把「是否租户管理员」「是否有 `tool.execute`」「工具是否对本租户可用」收敛在一个可单测的方法里。`_resource_tool_denial` 只在标准 grant 检查失败后再查询它，避免对已授权路径增加查询。

### 决策 2：豁免只跳过逐资源 grant，保留 `tool.execute` 功能权限

豁免的目的是让「本租户资源管理者」不被逐资源 grant 卡住，而不是免除功能权限。标准检查已要求 `permission="tool.execute"`；豁免方法同样要求该权限，因此租户若显式从 `tenant_admin` 角色移除 `tool.execute`，仍会被拒绝——功能权限与资源 grant 的语义边界保持不变。

### 决策 3：可用范围 = 租户可分配工具 execute 集合 ∪ 租户自有 MCP 工具

`tenant_admin_may_execute_tool` 的判据：

1. `_is_tenant_admin(user_id, tenant_id)` 为真（有效成员且成员角色含 `tenant_admin` 编码）；
2. `tool.execute` 在 `permissions_for(user_id, tenant_id)` 中；
3. `resource_id` 命中以下之一：
   - 在 `grantable_resource_ids(tenant_id, "tool", "execute", None)` 返回的集合中（即平台已开放给该租户的工具）；
   - 或以 `mcp:` 开头（database 模式下 MCP 连接配置在租户智能体工作区内，属租户自有；租户管理员无法运行未绑定本租户的智能体，故不会跨租户）。

第 3 条的第一项守住「平台对租户的开放上限」，第二项覆盖租户自有 MCP 工具。

### 决策 4：fail-closed 与判定顺序

`_resource_tool_denial` 的顺序保持：自授权工具放行 → 身份解析（失败即拒绝）→ `check_resource_action` → 命中即放行 → 未命中时查询豁免（`getattr(svc, "tenant_admin_may_execute_tool", None)`；缺失或非可调用即视为无豁免）→ 仍未命中则返回原拒绝。豁免方法抛出的异常由既有外层 `except` 捕获并 fail-closed。执行隔离仍在 `_permission_denial` 中先于本门禁执行，配额在本门禁之后，三者顺序不变。

## 风险与缓解

- **平台开放上限被绕过**：第 3 条第一项要求工具在租户可分配集合内，未开放的 builtin 工具仍拒绝。
- **跨租户**：豁免以当前身份解析出的 `tenant_id` 为界；MCP 工具来自调用者可运行的租户绑定智能体工作区。
- **放宽普通成员**：豁免先检查 `tenant_admin` 编码，普通成员走既有「功能权限 + 资源 grant」路径。
- **身份服务过时/异常**：每次调用重算，撤权下一次生效；方法异常时 fail-closed。

## Migration

1. 无数据迁移：不改表结构、不写授权数据。
2. 回滚：移除豁免方法调用即恢复旧判定，无需数据恢复。
3. 部署后新老租户一视同仁，租户管理员立即可执行本租户可用工具。
