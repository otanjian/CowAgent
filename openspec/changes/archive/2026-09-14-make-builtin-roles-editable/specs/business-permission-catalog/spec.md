## MODIFIED Requirements

### Requirement: 内置角色采用显式默认权限且升级不自动扩权

系统 SHALL 以显式登记的目录项定义 `member` 与 `tenant_admin` 的内置权限集合，并 SHALL 将其调整为：`member` 为「使用型 + 可创建自有资源」集合（`tenant.info.read`、`agent.read`、`agent.use`、`agent.edit`、`history.read`、`knowledge.read`、`memory.read`、`todo.read`、`todo.write`、`skill.read`、`skill.use`、`skill.edit`、`tool.read`、`tool.execute`、`tool.configure`、`model.read`、`model.use`、`chat.use`）；`tenant_admin` 为本 change 时点已登记的全部目录项（含 `knowledge.write`），且 SHALL 是 `member` 集合的超集。集合 MUST 以显式 id 枚举，MUST NOT 动态展开整个目录：新增目录项 MUST NOT 自动进入任一内置集合，必须显式修改集合才纳入。平台内置角色 SHALL 属于独立作用域，不进入 `member`/`tenant_admin` 的默认或显式权限并集。

内置角色的有效权限 SHALL 以其持久化权限集合为准；该角色无持久化集合时，SHALL 回落到本规范的显式默认集合。持久化集合为空 SHALL 被解释为「无业务权限」，MUST NOT 回落为默认集合。既有租户的内置角色 MUST 经版本化迁移回填为本规范的显式默认集合，迁移 MUST NOT 跨租户、MUST NOT 改动自定义角色，MUST NOT 使 `tenant_admin` 失去其集合内的任何权限。

#### Scenario: 升级保留既有角色权限

- **WHEN** 已有 tenant_admin、member 和自定义角色用户在本次迁移后再次请求有效权限
- **THEN** 内置角色分别取得本规范登记的使用型/全目录集合，自定义角色保持原选择并遵守原资源范围，迁移不对自定义角色新增任何执行权

#### Scenario: 管理员不能短路未登记业务权限

- **WHEN** tenant_admin 尝试请求未登记或未开放的业务执行能力
- **THEN** 请求按实际路由开放状态及权限检查被拒绝，不因管理员名称、内置集合或资源可读而放行

#### Scenario: 平台管理员自动包括新目录项

- **WHEN** 平台身份有效且目录新增合法动作
- **THEN** 平台all包括该动作，普通member/tenant_admin不会因扩目录自动获得，执行仍须满足运行条件

#### Scenario: 目录扩充不扩平台内置角色默认集合

- **WHEN** 功能目录新增合法业务动作
- **THEN** 平台内置角色的显式权限集合不因此自动写入新权限，平台范围能力仍由服务端资格派生，普通角色同样不自动获得

#### Scenario: 成员取得使用型与创建型权限

- **WHEN** 有效成员在已迁移租户中请求 `/auth/context` 或发起对话
- **THEN** 其有效权限包含 `chat.use`、`agent.use`、`model.use`、`skill.use`、`tool.execute` 及 `agent.edit`/`skill.edit`/`tool.configure`，可启动与租户共享默认智能体的对话，不再因缺少 `chat.use` 被拒

#### Scenario: 管理员取得全目录且显示与生效一致

- **WHEN** 有效租户管理员请求 `/api/tenant/roles` 与 `/auth/context`
- **THEN** 其内置角色持久化集合与有效权限都等于本规范的全部已登记目录项，控制台显示的权限集合与接口判定一致

#### Scenario: 回填不跨租户且保留自定义角色

- **WHEN** 版本化迁移在含多个租户的库中执行
- **THEN** 仅各租户的内置 `member`/`tenant_admin` 行被回填为显式默认集合，自定义角色的权限、资源授权与默认模型保持不变

#### Scenario: 空持久化集合不回落

- **WHEN** 某租户管理员显式将某内置角色的功能权限清空并保存
- **THEN** 该角色成员的有效权限为空集合，系统不回落为默认集合，除非该成员另有其他角色提供权限
