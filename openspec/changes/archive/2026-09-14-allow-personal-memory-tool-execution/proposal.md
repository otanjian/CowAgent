## Why

普通成员在对话中调用 `memory_search`（以及 `memory_add`、`memory_get`）时被拒，界面提示「此操作未被授权。执行权限由你的角色决定」。根因不是漏配 grant，而是**这三个工具的资源 id 根本进不了授权目录**：

- 它们是按 Agent 注入的依赖型工具（`bridge/agent_initializer.py` 里 `MemorySearchTool(memory_manager, user_id=...)`），
- `ToolManager._requires_injected_dependencies` 会在引擎级目录中跳过它们，因此 `list_tools()` 不含这三个工具名，
- 工具授权目录 `IdentityService._project_tools()` 由 `list_tools()` 生成，于是 `builtin:memory_search` / `memory_add` / `memory_get` 从不出现；
- 平台管理员配不出、租户开放集（`tenant_resource_grants`）没有、角色 grant（`role_resource_grants`）也无法选择；
- 但运行时 `AgentStreamExecutor._resource_tool_denial` 仍按 `builtin:<tool>` 要求 `tool.execute` + 资源 grant，且这三个工具未声明 `self_authorized`。

结果是：除平台管理员（`authorization_mode == "all"`）外，**任何租户、任何身份的成员都无法执行这三个工具**，且无法通过任何界面修复。已用真实身份库核对：全库 `role_resource_grants`/`tenant_resource_grants` 中不存在任何 memory 工具记录，租户 `AI启航团队` 的 builtin 开放集为 17 项、不含 memory 工具；配额表为空，可排除配额与隔离原因。

## What Changes

- 新增一条**按构造收窄**的执行授权豁免：调用者持有功能权限 `tool.execute` 与 `memory.read`、且是当前租户的有效成员时，本人作用域记忆工具（`memory_search` / `memory_get` / `memory_add`）不再要求逐资源 grant。
- 豁免只跳过"资源 grant"这一项：功能权限 `tool.execute`、`memory.read`、租户执行隔离、配额、Agent 工具范围全部保留。
- 写入侧按作用域收窄：`memory_add` 仅在 `scope` 不是 `shared`（即 `user` / `session` / 缺省）时豁免；`scope=shared` 写的是租户共享记忆，不属于"本人作用域"，继续按原规则拒绝。
- 不改变平台管理员行为（仍由 `authorization_mode == "all"` 放行），不改变其他任何工具的门禁，不新增权限目录条目、不新增 grant 表数据。

## Capabilities

### New Capabilities
<!-- 无新增 capability：本 change 只收窄既有执行授权规则 -->

### Modified Capabilities

- `resource-execution-authorization`: 「执行授权只由角色与隔离决定」requirement 增加"本人作用域记忆工具"的窄豁免与其边界场景（成员持 `tool.execute`+`memory.read` 放行；`scope=shared` 写入、缺 `memory.read`、跨租户身份仍拒绝）。

## Impact

- 代码：`auth/service.py`（新增窄豁免判定，与既有 `tenant_admin_may_execute_tool` 同处一处）、`agent/protocol/agent_stream.py`（`_resource_tool_denial` 接入，并传入调用参数以判定 `memory_add` 的 `scope`）。
- 数据：无 schema 变更、无 grant 迁移；豁免不落库、每次调用重算，撤权即时生效。
- 测试：新增针对窄豁免的单元/集成用例；既有 `tests/test_self_authorized_tools.py`、`tests/test_tenant_admin_tool_execution.py`、`tests/test_execution_authorization_fail_closed.py` 行为不变。
- 不影响：控制台工具授权页仍不列出这三个 id（它们不属于平台可分配目录），本 change 不承诺它们出现在授权界面。
