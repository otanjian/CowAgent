## 1. 身份服务

- [x] 1.1 `tenant_admin_may_execute_tool` 增加 `agent_id: Optional[str] = None`；`mcp:` 分支改为 `bool(agent_id) and agent_id in tenant_agent_ids(tenant_id)`，缺 `agent_id` 即拒绝。
- [x] 1.2 保持只读、逐次重算；builtin 分支不因新增参数改变判定。
- [x] 1.3 更新方法 docstring，说明前缀不携带租户信息、绑定查找才是实际校验。

## 2. 运行时接入

- [x] 2.1 `_resource_tool_denial` 以 `agent_id=ident.agent_id` 查询豁免。
- [x] 2.2 确认判定顺序、fail-closed 语义与自授权工具短路不变。

## 3. 测试

- [x] 3.1 RED：`tests/test_tenant_admin_tool_execution.py` 增加：绑定本租户的 Agent 的 `mcp:` 工具放行；绑定他租户、未绑定、`agent_id` 为 `None`/空 四种情形均拒绝；builtin 分支无需 `agent_id` 仍放行。
- [x] 3.2 运行时门禁把 `ident.agent_id` 透传给豁免。
- [x] 3.3 GREEN：20 用例全绿。

## 4. 校验与归档

- [x] 4.1 `openspec validate tighten-tenant-admin-mcp-exemption --strict` 通过。
- [x] 4.2 归档 change 并同步主规范。
