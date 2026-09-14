## Context

运行时门禁在 `agent/protocol/agent_stream.py`：`_permission_denial` 先跑租户执行隔离（`agent/permission/isolation.py`），再跑 `_resource_tool_denial`（`tool.execute` 功能权限 + 资源 grant，并带租户管理员豁免 `IdentityService.tenant_admin_may_execute_tool`），最后跑配额。

`_resource_tool_denial` 用 `_tool_resource_id()` 把工具名映射为资源 id：普通工具 `builtin:<name>`、MCP 工具 `mcp:<connection>:<name>`。

本人作用域记忆工具的现状：
- 由 `bridge/agent_initializer.py::_setup_memory_system` 按 Agent 注入（`MemorySearchTool` / `MemoryGetTool` / `MemoryAddTool`），需要 `MemoryManager` 与已验证 `user_id`；
- `agent/tools/tool_manager.py::_requires_injected_dependencies` 会在引擎级目录里跳过这类类，故 `list_tools()` 不含它们；
- 工具授权目录 `IdentityService._project_tools()` 由 `list_tools()` 生成，因此 `builtin:memory_search` / `memory_add` / `memory_get` 从不出现在任何可分配集合；
- 真实身份库核对（`tnt_EA3qM-lHPLD8ZPwW`）：租户 builtin 开放集 17 项、不含 memory 工具；全库 `role_resource_grants` + `tenant_resource_grants` 无任何 memory 工具记录；`quota_limits`/`quota_usage` 为空，排除配额原因。

因此现有规则下这三个工具对非平台管理员恒被拒绝，且无法通过界面授权。

## Goals / Non-Goals

**Goals:**

- 让持有 `tool.execute` 与 `memory.read` 的租户成员能执行本人作用域记忆工具，不再要求逐资源 grant。
- 豁免按构造收窄：只覆盖固定的三个工具名、只免除资源 grant 一项、写入侧排除 `scope=shared`。
- 不引入新的权限点、grant 数据或 schema 变更。

**Non-Goals:**

- 不把 memory 工具纳入平台/租户可分配目录，不改控制台授权页。
- 不改变平台管理员的放行路径（仍由 `authorization_mode == "all"`）。
- 不改变共享读取语义（`memory_search` 的 `include_shared=True` 维持既有行为）。
- 不放松执行隔离、配额、Agent 工具范围。

## Decisions

### D1 判定放在 `IdentityService`，与租户管理员豁免并列

新增 `IdentityService.personal_memory_tool_may_execute(user_id, tenant_id, tool_name, arguments=None) -> bool`，`agent_stream` 沿用对 `tenant_admin_may_execute_tool` 一样的 `getattr` + `callable` 探测：服务缺方法或抛异常时保留原拒绝（fail-closed）。

- 备选：新建 `agent/permission/` 模块（仿 `isolation.py`）。否决——判定需要 membership 与角色权限查询，独立模块要自取 service，重复且容易在协议层散落 DB 访问。
- 备选：给三个工具加 `self_authorized = True`（与 todo/scheduler 同法）。否决——该开关会连 `tool.execute` 功能门禁一起跳过，且 `memory_get` 无 user 维度、`memory_add` 可写 `shared`，豁免面偏宽；本次要求保留功能门禁。

### D2 豁免对象是代码内固定集合，由工具名匹配

`PERSONAL_MEMORY_TOOLS = ("memory_search", "memory_get", "memory_add")` 定义为代码常量；判定只按工具名，不用运行时构造的资源 id 做字符串匹配，避免"名字可伪造"成为放行依据。

### D3 `memory_add` 按作用域收窄，参数畸形不放行

判定函数接收调用参数，仅当 `scope` 不为 `"shared"` 时豁免（缺省视为 `user`，与工具默认一致）。`arguments` 不是 dict、或 `scope` 是异常类型时一律不豁免。

### D4 保留功能权限：`tool.execute` 且 `memory.read`

两者都要满足。`memory.read` 已在 `auth/policy.py::PERMISSION_CATALOG` 与 member 默认权限集内（现有租户成员均已持有），无需新增权限点，也不需要迁移历史角色。

### D5 接入点与签名兼容

`AgentStreamExecutor._resource_tool_denial(tool_name, arguments=None)`，由 `_permission_denial` 透传已在手的 `arguments`。保留默认参数，保证既有直接单参调用的测试不受影响。

### D6 不放宽其余门禁、不落库

豁免只替换"资源 grant 判定失败"这一步；隔离在前、配额在后不变。授权每次调用重算，`memory.read` / `tool.execute` 撤销即时生效。无 schema 与数据迁移。

### D7 记录在案的未决参数

`memory_get` 可读工作区范围的 `MEMORY.md` / `memory/...`（`memory/users/<uid>/...` 之外）与 `knowledge/...`，属于只读且受执行隔离约束。本次按"读语义"放行，不做路径级收窄；若后续要求更窄，可在同一判定函数内按 `path` 前缀加限。

## Risks / Trade-offs

- `memory_get` 的读取范围大于"本人域"：豁免后普通成员可用。缓解——只读、仍受执行隔离与 Agent 工具范围（工具必须在该 Agent 的工具集内）约束；更窄的路径级限制列为 D7 未决项。
- `memory_search` 默认包含共享记忆：这是既有行为，本 change 不改变，也不宣称共享读取已逐资源授权。
- 只持有 `memory.read`、未持 `tool.execute` 的成员仍读不到记忆：刻意的（保留功能门禁），如需放开须另立 change。
- 豁免不改变授权目录，控制台仍看不到这三个 id，可能被误读为"漏配"。已在能力规范中明确"豁免不改变授权目录内容"。
