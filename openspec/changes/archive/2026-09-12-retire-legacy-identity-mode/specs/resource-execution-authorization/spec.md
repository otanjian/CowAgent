## REMOVED Requirements

### Requirement: 数据库模式执行授权只由角色与隔离决定

**Reason**: 该要求包含「legacy 单租户安装 SHALL 保持原权限模式行为不变」及对应场景；legacy 删除后不存在旧会话权限模式参与授权的路径。

**Migration**: 改由新增 Requirement「执行授权只由角色与隔离决定」承载。

## ADDED Requirements

### Requirement: 执行授权只由角色与隔离决定

一次工具调用的执行授权 SHALL 只由租户执行隔离、角色的 `tool.execute` 功能权限与资源 grant、以及配额决定；会话或全局权限模式（read-only / workspace-write / full-access）MUST NOT 参与拒绝或放行。角色或资源 grant 的撤销 SHALL 在后续调用前生效。系统 MUST NOT 回退共享密码、免登录或旧模式的直接放行。

#### Scenario: 角色已授权但会话模式更低
- **WHEN** 某成员当前生效角色授予目标工具的 execute 资源动作与 `tool.execute`，而其会话权限模式为 read-only
- **THEN** 调用按角色授权执行，不因会话权限模式被拒绝

#### Scenario: 角色未授权工具
- **WHEN** 成员缺少目标工具的 `tool.execute` 或对应资源 grant
- **THEN** 调用在产生副作用前被拒绝，且拒绝结论不因会话权限模式而改变

#### Scenario: 身份不可解析
- **WHEN** 请求上下文未携带有效身份或身份服务查询失败
- **THEN** 执行被拒绝并记录可诊断告警，不降级为放行
