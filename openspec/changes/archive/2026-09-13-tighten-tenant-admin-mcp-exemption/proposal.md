## Why

`allow-tenant-admin-tool-execution`（已归档）引入了「租户管理员对本租户可用工具的执行豁免」。其中 MCP 分支的判据是 `resource_id` 以 `mcp:` 开头即视为租户自有。

该前缀本身不携带租户信息：`mcp:<connection>:<tool>` 命名的是**连接**，不是租户。任何能拼出该字符串的调用者都能借此跳过逐资源 grant，因此该分支并未真正校验「连接属于本租户」。

本 change 把 MCP 分支收紧为**显式 Agent 绑定查找**：`mcp:` 资源仅在调用者的运行时 Agent 绑定于当前租户时才放行。这与既有 `_tenant_admin_owns_agent` 依赖的隔离边界一致，属同一判据的复用，而非新增信任。

## What Changes

- `IdentityService.tenant_admin_may_execute_tool(...)` 新增 `agent_id` 参数（调用者的 `RuntimeIdentity.agent_id`）。当 `resource_id` 以 `mcp:` 开头时，SHALL 满足 `agent_id in tenant_agent_ids(tenant_id)`，否则拒绝；`agent_id` 缺失或为空时一律拒绝（fail closed）。
- `agent/protocol/agent_stream.py::_resource_tool_denial` 在查询豁免时传入 `ident.agent_id`。
- builtin 分支不变：其 id 仍与租户自身 grant 集合比对，不需要 `agent_id`。
- 不放宽执行隔离、配额、平台开放上限或普通成员授权。

## Capabilities

### Modified Capabilities
- `resource-execution-authorization`: 「执行授权只由角色与隔离决定」中明确 MCP 豁免须以 Agent 绑定为前置。

## Impact

- 后端：`auth/service.py`、`agent/protocol/agent_stream.py`。
- 测试：`tests/test_tenant_admin_tool_execution.py` 增加绑定维度的正反用例（本租户绑定放行；他租户绑定、未绑定、缺 `agent_id` 均拒绝）。
- 无数据迁移：绑定关系读取既有 `agent_bindings`；豁免每次调用重算。
- 行为变化仅限收紧：此前会被放行的「无绑定 `mcp:` id」现在被拒绝；本租户绑定 Agent 的 MCP 工具行为不变。
