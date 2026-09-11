## ADDED Requirements

### Requirement: 数据库模式执行授权只由角色与隔离决定
在 database 身份模式下，一次工具调用的执行授权 SHALL 只由租户执行隔离、角色的 `tool.execute` 功能权限与资源 grant、以及配额决定；legacy 会话权限模式（read-only / workspace-write / full-access）MUST NOT 参与拒绝或放行。legacy 单租户安装 SHALL 保持原权限模式行为不变。角色或资源 grant 的撤销 SHALL 在后续调用前生效。

#### Scenario: 角色已授权但会话模式更低
- **WHEN** database 模式中某成员当前生效角色授予目标工具的 execute 资源动作与 `tool.execute`，而其会话权限模式为 read-only
- **THEN** 调用按角色授权执行，不因 legacy 模式被拒绝

#### Scenario: 角色未授权工具
- **WHEN** database 模式中某成员缺少目标工具的 `tool.execute` 或对应资源 grant
- **THEN** 调用在产生副作用前被拒绝，且拒绝结论不因会话权限模式而改变

#### Scenario: legacy 单租户保持模式行为
- **WHEN** 非 database 的 legacy 安装将会话或全局权限模式设为 read-only
- **THEN** 写入类工具仍按原模式被拒绝
