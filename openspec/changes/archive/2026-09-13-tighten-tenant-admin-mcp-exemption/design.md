## Context

豁免的 MCP 分支原判据是 `resource_id.startswith("mcp:")`。`resource_id` 由 `_tool_resource_id` 从 `McpTool.server_name` 组装为 `mcp:<connection>:<tool>`，只标识连接名，不含租户。因此该判据实际是「字符串前缀匹配」，而不是「连接属于本租户」。

真正界定 MCP 连接归属的是 Agent 绑定：MCP 连接声明在 Agent 工作区的 `mcp.json` 中，而 `agent_bindings` 是「哪个租户可运行哪个 Agent」的唯一授权记录（`web_channel._tenant_admin_owns_agent` 即以此为判据）。

## Goals / Non-Goals

**Goals**
- MCP 豁免以显式绑定查找为前提，前缀不再构成信任。
- 本租户绑定 Agent 的 MCP 工具行为不变（仍放行）。
- builtin 分支、隔离、配额、开放上限、普通成员授权全部不变。

**Non-Goals**
- 不改 `agent_bindings` 结构，不引入新的归属模型。
- 不校验 `server_name` 是否真的出现在该 Agent 的 `mcp.json` 中：绑定已是「谁可运行此 Agent」的授权边界，Agent 工作区内的连接随 Agent 归属；额外的文件级校验会引入运行期 IO 与缓存一致性问题，收益不足。

## Decisions

### 决策 1：绑定查找复用 `tenant_agent_ids`

判据为 `agent_id in set(self.tenant_agent_ids(tenant_id))`，与 `_tenant_admin_owns_agent` 同源，避免第二套归属判断逻辑漂移。

### 决策 2：`agent_id` 缺失即拒绝

`RuntimeIdentity.agent_id` 在 chat 运行期由 `chat_channel._identity_for` 解析：优先取快照的 `agent_id`，否则回落到 `context["agent_id"]`（即 `resolved_agent_id`）。无法解析出 Agent 时说明运行上下文不完整，此时按 fail-closed 拒绝 MCP 豁免，而不是假设其属于本租户。

### 决策 3：由运行时门禁传入，不由服务反查

服务层拿不到「本次运行的是哪个 Agent」；从 `resource_id` 反查连接名到 Agent 需要遍历工作区与读取 `mcp.json`，既慢又依赖缓存。`RuntimeIdentity.agent_id` 已是本进程内已验证的运行主体，直接透传最准确。

### 决策 4：builtin 分支不要求 `agent_id`

builtin 资源 id 与租户自身 grant 集合比对，本就不依赖 Agent 身份；要求 `agent_id` 会无谓收窄合法路径（如调度器等无 Agent 上下文调用）。

## 风险与缓解

- **行为收紧导致回归**：仅影响此前依赖「无绑定 `mcp:` 前缀」放行的调用；本租户绑定路径已由 E2E 用例覆盖。
- **`agent_id` 未填充**：已在 live 数据上核对绑定关系与解析链路；若解析失败则拒绝而非放行。
- **绑定关系变更**：每次调用重算，绑定撤销下一次生效。

## Migration

1. 无数据迁移：只读 `agent_bindings`。
2. 回滚：移除绑定判断即恢复前缀放行，无需数据恢复。
