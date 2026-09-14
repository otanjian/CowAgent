## MODIFIED Requirements

### Requirement: 执行授权只由角色与隔离决定

一次工具调用的执行授权 SHALL 由租户执行隔离、角色的 `tool.execute` 功能权限与资源 grant、以及配额决定；会话或全局权限模式（read-only / workspace-write / full-access）MUST NOT 参与拒绝或放行。租户管理员 SHALL 在不持有目标工具资源 grant 时仍可执行本租户可用工具：调用者须为当前租户的有效 `tenant_admin` 成员、仍持有 `tool.execute` 功能权限，且目标工具对本租户可用——`resource_id` 在租户可分配的工具 execute 集合内，或为 `mcp:<connection>:<tool>` 形式的租户自有 MCP 工具且**调用者的运行时 Agent 绑定于该租户**（`agent_id` 属于该租户的 `agent_bindings`）。MCP 资源 id 的 `mcp:` 前缀本身不携带租户信息，MUST NOT 单独作为放行依据；`agent_id` 缺失或为空时该分支 MUST 拒绝。该豁免 MUST NOT 绕过执行隔离、配额、Agent 工具范围或平台对租户的开放上限，也 MUST NOT 扩展到普通成员。角色或资源 grant 的撤销 SHALL 在后续调用前生效。系统 MUST NOT 回退共享密码、免登录或旧模式的直接放行。

#### Scenario: 角色已授权但会话模式更低
- **WHEN** 某成员当前生效角色授予目标工具的 execute 资源动作与 `tool.execute`，而其会话权限模式为 read-only
- **THEN** 调用按角色授权执行，不因会话权限模式被拒绝

#### Scenario: 租户管理员执行本租户可用但未逐资源 grant 的工具
- **WHEN** 某租户管理员是其租户的有效成员且持有 `tool.execute`，目标工具为平台已开放给该租户的内置工具，或为绑定于该租户的 Agent 所声明的 MCP 工具，而其角色未携带该工具的资源 grant
- **THEN** 调用放行，且仍受执行隔离、配额与 Agent 工具范围约束

#### Scenario: 无绑定 Agent 的 MCP 工具被拒绝
- **WHEN** 租户管理员以 `mcp:<connection>:<tool>` 形式的资源 id 请求执行，但其运行时 `agent_id` 不属于该租户（绑定于其他租户、未绑定，或缺失）
- **THEN** 调用被拒绝，`mcp:` 前缀本身不构成放行依据

#### Scenario: 租户管理员跨租户或超出开放上限被拒绝
- **WHEN** 租户管理员尝试执行平台未开放给该租户的内置工具，或其他租户的工具
- **THEN** 调用被拒绝，豁免不放宽租户资源上限或跨租户边界

#### Scenario: 角色未授权工具
- **WHEN** 普通成员（非租户管理员）缺少目标工具的 `tool.execute` 或对应资源 grant
- **THEN** 调用在产生副作用前被拒绝，且拒绝结论不因会话权限模式而改变

#### Scenario: 身份不可解析
- **WHEN** 请求上下文未携带有效身份或身份服务查询失败
- **THEN** 执行被拒绝并记录可诊断告警，不降级为放行
