## 1. 身份服务

- [x] 1.1 `auth/service.py`：新增 `tenant_admin_may_execute_tool(user_id, tenant_id, resource_id) -> bool`，判据为 `_is_tenant_admin` + `tool.execute` 功能权限 +（resource_id 在 `grantable_resource_ids(tenant_id, "tool", "execute", None)` 内 或 以 `mcp:` 开头）；身份/租户缺失返回 False，不抛出对调用方无意义的异常。
- [x] 1.2 方法为只读、逐次重算，不缓存；不写审计（放行不是写入事件）。

## 2. 运行时接入

- [x] 2.1 `agent/protocol/agent_stream.py::_resource_tool_denial`：标准 `check_resource_action` 失败后，用 `getattr(svc, "tenant_admin_may_execute_tool", None)` 查询豁免；命中返回 `None`，缺失/非可调用视为无豁免，异常由既有外层 fail-closed 捕获。
- [x] 2.2 确认执行隔离、配额、自授权工具的判定顺序与语义不变。

## 3. 测试

- [x] 3.1 RED：新增 `tests/test_tenant_admin_tool_execution.py`，覆盖：
  - 租户管理员 + `tool.execute` + 租户已开放 builtin 工具、无 role grant → 放行；
  - 租户管理员 + 租户自有 MCP 工具（`mcp:...`）、无 role grant → 放行；
  - 租户管理员 + 平台未开放给该租户的 builtin 工具、无 role grant → 拒绝；
  - 租户管理员但角色不含 `tool.execute` → 拒绝；
  - 普通成员无 grant → 拒绝；
  - 移除 `tenant_admin` 后下一次调用 → 拒绝；
  - 身份服务不支持/异常 → fail-closed 拒绝。
- [x] 3.2 GREEN：实现后跑绿；回归 `tests/test_execution_authorization_fail_closed.py`、`tests/test_self_authorized_tools.py`、`tests/test_identity_boundary_contract.py`。
- [x] 3.3 运行 `python -m pytest` 相关用例并确认通过。

## 4. 校验与归档

- [x] 4.1 `openspec validate allow-tenant-admin-tool-execution --strict` 通过。
- [ ] 4.2 实现与测试完成后归档 change，并同步主规范。
