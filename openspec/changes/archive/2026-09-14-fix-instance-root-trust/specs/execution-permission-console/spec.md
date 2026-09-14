## MODIFIED Requirements

### Requirement: 被拒提示仅说明角色授权原因

工具被拒提示 SHALL 说明拒绝来源，并按拒绝种类（`permission_denial_kind`）给出对应文案：由角色授权拒绝 SHALL 说明当前角色未获授权；由执行隔离拒绝 SHALL 说明隔离边界原因（仅可访问本租户工作根与状态目录），MUST NOT 套用「执行权限由您的角色决定」的角色文案；配额与身份不可解析拒绝 SHALL 说明各自原因。任何拒绝 MUST NOT 显示会话权限模式或暗示可通过聊天调整，MUST NOT 提供共享密码模式下的模式文案。

#### Scenario: 角色拒绝
- **WHEN** 工具因缺少 `tool.execute` 或对应资源 grant 被拒绝
- **THEN** 提示说明当前角色未获授权并可联系管理员，不显示"当前权限为…"的模式文案

#### Scenario: 执行隔离拒绝
- **WHEN** 工具因执行隔离边界被拒绝
- **THEN** 提示说明隔离边界原因，不提供自行调整执行权限的入口，且不显示「执行权限由您的角色决定」的角色文案

#### Scenario: 隔离拒绝的文案区分
- **WHEN** 工具调用事件的 `permission_denial_kind` 为 `isolation`
- **THEN** 对话界面渲染隔离边界说明，而非角色授权说明
