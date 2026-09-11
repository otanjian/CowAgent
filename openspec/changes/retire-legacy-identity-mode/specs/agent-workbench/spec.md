## REMOVED Requirements

### Requirement: 租户自建智能体在创建时即归属该租户

**Reason**: 该要求包含「legacy 模式（无租户身份）MUST NOT 进入租户归属逻辑」及「legacy 模式」场景；legacy 删除后不存在无租户身份的创建路径。

**Migration**: 改由新增 Requirement「租户自建智能体创建即归属该租户」承载。

## ADDED Requirements

### Requirement: 租户自建智能体创建即归属该租户

租户从控制台创建智能体时，系统 SHALL 在同一请求内完成三件事：把工作区创建在该租户共享根下的 `<shared_root>/agents/<id>`；把新智能体写入租户绑定；在该租户此前没有任何绑定智能体且未配置默认智能体时，将其设为其租户默认。客户端显式提交 `workspace` 时，工作区以客户端为准，但绑定规则不变。租户无已解析共享根时，系统 SHALL 沿用既有实例根缺省工作区，但 MUST NOT 省略租户绑定。系统 MUST NOT 提供无租户身份的创建路径。

#### Scenario: 租户管理员创建首个智能体
- **WHEN** 租户管理员在控制台提交创建，且其租户当前没有任何绑定智能体、也没有配置默认智能体
- **THEN** 工作区落在该租户共享根下的 `<shared_root>/agents/<id>`，`agent_bindings` 新增该租户绑定，`tenants.default_agent_id` 更新为该智能体，下一次列表读取可见它且标记为默认

#### Scenario: 租户已有默认智能体
- **WHEN** 租户已配置默认智能体，管理员再创建一个智能体
- **THEN** 新智能体被绑定并可见，既有默认智能体 MUST NOT 被改动

#### Scenario: 显式指定工作区
- **WHEN** 客户端在创建请求中显式提交 `workspace`
- **THEN** 使用客户端给出的工作区，其余归属规则（绑定、首个默认承接）不变

#### Scenario: 非法智能体标识
- **WHEN** 创建请求的 `id` 不满足智能体标识规则（例如包含路径分隔或 `..`）
- **THEN** 系统拒绝该请求，MUST NOT 创建任何目录，包括租户共享根之外的目录

#### Scenario: 缺少租户上下文
- **WHEN** 创建请求没有有效租户身份
- **THEN** 系统拒绝该请求，MUST NOT 在实例根下创建智能体目录或省略租户绑定
