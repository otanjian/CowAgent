## Why

租户管理员在对话中调用本租户智能体已配置的 ERP MCP 工具时被拒绝，界面提示「此操作未被授权。执行权限由你的角色决定，如需使用请联系管理员」。

根因是两层叠加：

- 工具执行在 `agent/protocol/agent_stream.py::_resource_tool_denial` 要求同时具备 `tool.execute` 功能权限与 `(tool, resource_id, execute)` 资源 grant。内置 `tenant_admin` 角色持有前者，但不携带任何资源 grant。
- 租户自有的 MCP 工具（配置在租户智能体工作区的 `mcp.json`）不在 `tenant_resource_grants` 中，且工具类别的 `_project_owned_ids` 返回 `None`（视为平台全局），因此这些工具既不能被租户管理员指派给角色，也无从执行。

这与《产品规划》3.1「租户管理员负责本租户智能体、工具与技能的维护」不一致，也与既有「租户管理员对本租户绑定智能体免逐资源 grant」先例不一致。普通成员仍须逐资源授权，但租户管理员作为本租户资源管理者，应能执行本租户可用工具。

## What Changes

- 新增**租户管理员对本租户可用工具的执行豁免**：database 模式下，工具调用的标准授权（`tool.execute` 功能权限 + 资源 grant）判定失败后，若调用者为当前租户有效 `tenant_admin`、仍持有 `tool.execute`，且目标工具对本租户可用（其 `resource_id` 在租户可分配的工具 execute 集合内，或为租户自有 MCP 工具 `mcp:<connection>:<tool>`），则放行。
- 豁免**不放宽**执行隔离、配额、Agent 工具范围与平台对租户的开放上限，也**不扩展**到普通成员。
- 平台管理员（`all`）与普通成员的授权行为保持不变。

## Capabilities

### Modified Capabilities
- `resource-execution-authorization`: 在「执行授权只由角色与隔离决定」中明确租户管理员对本租户可用工具的执行豁免及其边界。

## Impact

- 后端：`auth/service.py` 新增公开方法 `tenant_admin_may_execute_tool(...)`；`agent/protocol/agent_stream.py` 的 `_resource_tool_denial` 在标准检查失败后查询该豁免，服务不支持或异常时保持 fail-closed。
- 测试：新增 `tests/test_tenant_admin_tool_execution.py`；回归 `tests/test_execution_authorization_fail_closed.py`、`tests/test_self_authorized_tools.py`。
- 不改动：`tenant_resource_grants` / `role_resource_grants` 表结构与既有数据；成员逐资源授权；执行隔离与配额门槛。
- 无数据迁移：豁免在每次调用时按当前身份与租户授权重算。
