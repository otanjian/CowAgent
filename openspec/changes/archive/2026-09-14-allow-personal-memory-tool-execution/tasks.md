## 1. 窄豁免判定（身份服务）

- [x] 1.1 `auth/service.py`：定义代码内固定集合 `PERSONAL_MEMORY_TOOLS = ("memory_search", "memory_get", "memory_add")`，不按资源 id 字符串匹配
- [x] 1.2 `auth/service.py`：新增 `personal_memory_tool_may_execute(user_id, tenant_id, tool_name, arguments=None)`，与 `tenant_admin_may_execute_tool` 并列；条件为当前租户有效成员 + 持有 `tool.execute` + 持有 `memory.read`
- [x] 1.3 该判定内按作用域收窄 `memory_add`：`scope` 非 `"shared"`（缺省视为 `user`）才豁免；`arguments` 非 dict 或 `scope` 类型异常时不豁免
- [x] 1.4 fail-closed：`user_id`/`tenant_id` 为空、非成员、未知工具名一律返回 False，不抛异常
- [x] 1.5 先写失败测试（见 3.1），再实现 1.1–1.4

## 2. 运行时接入

- [x] 2.1 `agent/protocol/agent_stream.py`：`_resource_tool_denial(tool_name, arguments=None)`，保留默认参数以兼容既有单参调用
- [x] 2.2 `_permission_denial` 把已在手的 `arguments` 透传给 `_resource_tool_denial`
- [x] 2.3 仅在 `check_resource_action` 失败后尝试该豁免，`getattr` + `callable` 探测，服务缺方法或抛异常时保留原拒绝；隔离（前）与配额（后）判定顺序与语义不变
- [x] 2.4 确认平台管理员路径不变（仍经 `authorization_mode == "all"` 直接放行，不依赖本豁免）

## 3. 测试

- [x] 3.1 复现用例（先失败）：用真实身份库构造「普通成员 + `tool.execute` + `memory.read` + 无 memory 工具 grant」，断言修复前 `memory_search` / `memory_add(user)` 被拒（kind 为 role）
- [x] 3.2 修复后：同一身份 `memory_search`、`memory_get`、`memory_add`（缺省与 `scope=user`/`session`）放行
- [x] 3.3 `memory_add` 的 `scope=shared` 仍被拒绝，且不写入共享记忆
- [x] 3.4 缺 `memory.read`（或 `role_resource_grants` 被清）时三个工具仍被拒绝
- [x] 3.5 撤销 `memory.read` 后下一次调用被拒绝（不缓存结论）
- [x] 3.6 非记忆工具不受影响：未获 grant 的 `web_search` 之类仍被拒绝；`scope` 参数畸形不构成放行
- [x] 3.7 回归：`tests/test_self_authorized_tools.py`、`tests/test_tenant_admin_tool_execution.py`、`tests/test_execution_authorization_fail_closed.py`、`tests/test_rbac_execution_permission.py`、`tests/test_identity_boundary_contract.py` 全部通过

## 4. 验收

- [x] 4.1 `openspec validate allow-personal-memory-tool-execution --strict` 通过
- [x] 4.2 用真实身份库脚本核对：豁免不写任何 grant、不改 schema；控制台可分配集合仍不含 memory 工具（豁免是执行路径窄通道）
- [x] 4.3 核对既有能力无回归：`resource-execution-authorization` 原场景（租户管理员豁免、MCP 前缀不作依据、身份不可解析 fail-closed）逐一复跑

## 5. 已知遗留（不在本 change 范围）

- [ ] 5.1 `tests/test_execution_authorization_fail_closed.py` 的两个 legacy 用例在 `HEAD`(61686eb2) 即失败（`_permission_denial`/`_resource_tool_denial` 已无 `database_mode` 分支）：与本 change 无关，另行处理
- [ ] 5.2 `memory_get` 的路径级收窄（`memory/users/<uid>/...` 之外）列为 design D7 未决项，如需更窄另立 change
- [ ] 5.3 memory 工具仍未进入平台可分配目录，控制台工具授权页不显示它们——本次刻意不改变
