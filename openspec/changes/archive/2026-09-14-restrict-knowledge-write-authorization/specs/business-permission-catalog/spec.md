## MODIFIED Requirements

### Requirement: 权限目录有限且具有稳定元数据

系统 SHALL 保留原九项权限ID并增加本次明确登记的 skill.read/use/edit/enable、tool.read/execute/configure、model.read/use、agent.use/edit/enable、chat.use 十三项功能动作，提供稳定id、group、label、description、scope和assignable元数据。知识库写入 MUST NOT 作为可分配的目录项存在：目录中不再包含 `knowledge.write`，知识库写入改由数据根归属与智能体归属判定（见 `tenant-knowledge-console`），MUST NOT 被解释为读取能力的替代或包含关系——读取仍由 `knowledge.read` 独立判定。自定义角色 SHALL 仅能保存允许分配的目录项，未知ID、客户端通配符、平台标识或身份管理员资格不得保存。平台all由权威系统资格派生，不写普通角色；不按前缀推导权限，不预登记SSO或未适配客户端权限。

#### Scenario: 展示并选择登记权限

- **WHEN** 租户管理员为自定义角色查看和选择可分配权限
- **THEN** 服务端返回分组、用途及作用域明确的目录，其中「知识」分组只提供「查看知识」的 `knowledge.read`，不返回任何知识写入权限项，保存后仅授予所选择且允许分配的稳定权限 ID

#### Scenario: 授予知识写入权限

- **WHEN** 租户管理员尝试为自定义角色选择知识写入权限，或角色写请求显式包含 `knowledge.write`
- **THEN** 目录中不存在该可分配项，系统按尚未登记 ID 拒绝整次变更，不保存部分权限；知识库写入能力 MUST NOT 因角色配置而获得

#### Scenario: 提交未知或不可分配权限

- **WHEN** 角色写请求包含尚未登记 ID、通配符、平台管理员标识或身份审计权限
- **THEN** 系统拒绝整次变更，不保存部分权限或将输入解释为隐式管理员资格

### Requirement: 内置角色采用显式默认权限且升级不自动扩权

系统 SHALL 以显式登记的目录项定义 `member` 与 `tenant_admin` 的内置权限集合，并 SHALL 将其调整为：`member` 为「使用型 + 可创建自有资源」集合（`tenant.info.read`、`agent.read`、`agent.use`、`agent.edit`、`history.read`、`knowledge.read`、`memory.read`、`todo.read`、`todo.write`、`skill.read`、`skill.use`、`skill.edit`、`tool.read`、`tool.execute`、`tool.configure`、`model.read`、`model.use`、`chat.use`）；`tenant_admin` 为已登记的全部目录项，且 SHALL 是 `member` 集合的超集。集合 MUST 以显式 id 枚举，MUST NOT 动态展开整个目录：新增目录项 MUST NOT 自动进入任一内置集合，必须显式修改集合才纳入。平台内置角色 SHALL 属于独立作用域，不进入 `member`/`tenant_admin` 的默认或显式权限并集。

内置角色的有效权限 SHALL 以其持久化权限集合为准；该角色无持久化集合时，SHALL 回落到本规范的显式默认集合。持久化集合为空 SHALL 被解释为「无业务权限」，MUST NOT 回落为默认集合。既有租户的内置角色 MUST 经版本化迁移回填为本规范的显式默认集合，迁移 MUST NOT 跨租户、MUST NOT 改动自定义角色，MUST NOT 使 `tenant_admin` 失去其集合内的任何权限。

目录移除既有权限项时，SHALL 以版本化迁移把该 id 从**所有**角色的持久化权限集合中剥离（内置与自定义一视同仁），MUST NOT 改动同一集合中的其它权限，MUST NOT 使任何角色因此获得新的权限；被剥离的 id 在剥离后 MUST NOT 再作为授权依据。

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

#### Scenario: 移除目录项后存量角色不再持有

- **WHEN** 权限目录移除某项后，版本化迁移在含该 id 的内置与自定义角色上执行
- **THEN** 每个角色的持久化集合中该 id 被剥离、版本号递增，同一集合中的其它权限保持不变，且该 id 在后续授权判定中不再产生任何效果
