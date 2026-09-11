## REMOVED Requirements

### Requirement: 被拒提示说明真实原因

**Reason**: 该要求与场景包含 legacy 权限模式拒绝文案；legacy 删除后工具拒绝只可能来自角色授权或执行隔离。

**Migration**: 改由新增 Requirement「被拒提示仅说明角色授权原因」承载。

### Requirement: 全局默认权限在数据库模式下只读

**Reason**: 该要求保留「legacy 模式可编辑」分支；legacy 删除后全局默认权限统一只读。

**Migration**: 改由新增 Requirement「全局默认权限统一只读」承载。

## ADDED Requirements

### Requirement: 被拒提示仅说明角色授权原因

工具被拒提示 SHALL 说明拒绝来源。由角色授权或执行隔离拒绝 SHALL 说明当前角色未获授权，MUST NOT 显示会话权限模式或暗示可通过聊天调整，MUST NOT 提供共享密码模式下的模式文案。

#### Scenario: 角色拒绝
- **WHEN** 工具因缺少 `tool.execute` 或对应资源 grant 被拒绝
- **THEN** 提示说明当前角色未获授权并可联系管理员，不显示"当前权限为…"的模式文案

#### Scenario: 执行隔离拒绝
- **WHEN** 工具因执行隔离边界被拒绝
- **THEN** 提示说明隔离边界原因，不提供自行调整执行权限的入口

### Requirement: 全局默认权限统一只读

平台设置页的全局「默认权限」配置 SHALL 只读展示并说明执行权限由角色资源授权控制，MUST NOT 允许通过该设置改变实际执行授权。

#### Scenario: 平台管理员查看默认权限
- **WHEN** 平台管理员打开设置页
- **THEN** 「默认权限」不可编辑并显示由角色授权控制的说明

#### Scenario: 尝试修改默认权限
- **WHEN** 客户端提交全局「默认权限」变更
- **THEN** 服务端不应用该变更，实际工具授权仍由角色与资源 grant 决定

## MODIFIED Requirements

### Requirement: 会话设置接口不接受数据库模式覆盖

会话设置接口 SHALL 不返回可用于自行调整的权限模式集合，且 MUST NOT 接受或持久化会话级 permission 覆盖。

#### Scenario: 数据库模式提交会话权限覆盖
- **WHEN** 客户端提交 `permission` 会话设置
- **THEN** 服务端不应用该覆盖，实际工具授权仍由角色决定
