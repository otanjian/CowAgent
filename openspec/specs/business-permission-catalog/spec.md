# business-permission-catalog Specification

## Purpose
为 CowAgent 已有九项功能权限补齐统一元数据、内置显式默认集合与当前租户的本人有效能力查询，保持身份管理资格和业务访问范围的边界。本次仅完善现有身份安全及管理页面，不预登记未来执行权限，也不借目录调整开放尚未适配的业务或客户端。
## Requirements
### Requirement: 权限目录有限且具有稳定元数据

系统 SHALL 保留原九项权限ID并增加本次明确登记的 skill.read/use/edit/enable、tool.read/execute/configure、model.read/use、agent.use/edit/enable、chat.use 十三项功能动作，以及知识库写入所需的 knowledge.write，提供稳定id、group、label、description、scope和assignable元数据。自定义角色 SHALL 仅能保存允许分配的目录项，未知ID、客户端通配符、平台标识或身份管理员资格不得保存。平台all由权威系统资格派生，不写普通角色；不按前缀推导权限，不预登记SSO或未适配客户端权限。knowledge.write SHALL 归类为租户作用域且可分配，MUST NOT 被解释为读取能力的替代或包含关系——读取仍由 knowledge.read 独立判定。

#### Scenario: 展示并选择登记权限

- **WHEN** 租户管理员为自定义角色查看和选择可分配权限
- **THEN** 服务端返回分组、用途及作用域明确的目录，包含「知识 → 编辑知识」的 knowledge.write，保存后仅授予所选择且允许分配的稳定权限 ID

#### Scenario: 授予知识写入权限

- **WHEN** 租户管理员为自定义角色选择 knowledge.write 并保存
- **THEN** 该角色成员在后续请求中取得 knowledge.write，可执行知识库写入；未选择该权限的角色成员不因此获得写入能力

#### Scenario: 提交未知或不可分配权限

- **WHEN** 角色写请求包含尚未登记 ID、通配符、平台管理员标识或身份审计权限
- **THEN** 系统拒绝整次变更，不保存部分权限或将输入解释为隐式管理员资格

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

### Requirement: 平台控制面与租户身份管理资格独立

平台管理员 SHALL 拥有已登记功能和资源的系统all，并通过明确目标租户的专用平台接口管理角色授权和资源分配，目标独立验证且记录审计。平台资格 SHALL 由平台作用域的内置角色绑定承载，与租户作用域身份管理资格分属不同作用域；租户接口的身份管理仍由当前有效tenant_admin控制，普通角色并集、部门或岗位不得产生平台/租户管理资格。平台all MUST NOT 自动建立Membership或代用目标成员执行业务；每个启用账号仍具备有效租户归属，正常业务保持租户、owner及执行约束。未适配消费者不因任何管理员资格开放。

#### Scenario: 平台管理员无业务成员资格
- **WHEN** 未加入目标租户的平台管理员经普通租户接口请求该租户聊天、成员写入或私有资源
- **THEN** 系统拒绝访问，平台管理资格不能充当目标租户成员、身份管理员或业务授权

#### Scenario: 自定义角色请求身份或全局配置管理
- **WHEN** 普通成员持有多个自定义业务权限并尝试授予身份管理员资格，或租户管理员请求尚未租户化的全局配置写入
- **THEN** 系统依据身份域和功能启用状态拒绝，角色权限数量及 tenant_admin 身份不能越过该边界

#### Scenario: 平台内置角色不可经目录分配
- **WHEN** 租户侧角色配置界面或保存请求尝试选择、复制或分配平台内置角色
- **THEN** 系统不将平台角色作为可分配目录项返回或保存，并拒绝试图把平台资格写入租户角色权限的变更

### Requirement: 有效权限上下文只补当前租户展示能力

GET /auth/context SHALL 根据有效会话及X-Tenant-ID验证本人在所选租户的有效成员资格，保留effective_permissions、管理资格及消费者状态，增加authorization_mode和逐页面/页签/动作投影。资源目录另按需分页，响应不返回其他成员、全租户权限或敏感配置。接口 MUST NOT 要求tenant.info.read，零业务权限有效成员仍能读取空能力；缺少选择400、无有效成员403、服务故障503，受限改密会话不得读取租户能力。/auth/me保持本人及有效租户摘要，不持久化权限副本；平台目标管理能力由相应平台接口独立提供，不改变业务租户或AuthSession。后续API SHALL 独立重新授权，Desktop未适配能力保持标示关闭。

#### Scenario: 页面加载当前能力
- **WHEN** 有效成员已通过 /auth/me 确认身份和租户后请求 /auth/context
- **THEN** 响应只补充本人在该租户的权限、管理资格与消费者状态，Web 按结果展示入口并继续使用既有个人资料来源

#### Scenario: 零业务权限成员读取本人能力
- **WHEN** 有效成员只有空权限自定义角色且没有 tenant.info.read，并携带所选有效租户请求 /auth/context
- **THEN** 系统返回 200 和空 effective_permissions，不返回租户资料，也不因能力查询赋予任何名录读取或管理权限

#### Scenario: 所选租户无成员资格或身份服务故障
- **WHEN** 用户请求未加入或已停用成员关系的租户上下文，或身份服务无法读取当前事实
- **THEN** 分别返回 403 或 503，不返回其他租户能力，不回退缓存或默认租户

#### Scenario: 伪造或复用旧上下文
- **WHEN** 客户端篡改 effective_permissions，或在角色撤销后提交旧 /auth/context 响应中的权限
- **THEN** 后续 API 独立使用当前身份事实重新授权，不因客户端声明或旧页面能力放行

### Requirement: 多角色权限实时合并且受资源和执行约束

有效成员的功能权限 SHALL 取当前租户有效角色的显式并集；资源动作grant亦取允许并集，再与租户资源上限、原归属/owner、Agent限制和消费者状态共同校验。平台all仅跳过功能和资源选择条件。每次请求及实际调用 SHALL 验证最新授权，撤销最后来源后下一次拒绝，仍有来源则保留；部门变化不隐式授权。模型默认值不得产生资源grant，授权空集合必须拒绝，角色资源授权不得扩大个人历史/待办等数据范围。

具体资源grant门槛 SHALL 仅适用于本次菜单、技能、工具、模型、Agent，其他数据继续按原功能和owner判断，不增加history/todo等资源grant要求。

#### Scenario: 撤销一个重复权限来源
- **WHEN** 成员两个角色均授予同一权限，其中一个角色被撤销
- **THEN** 当前权限仍可由剩余角色提供；撤销最后有效来源后下一请求被拒绝，旧能力上下文不能延长授权

#### Scenario: 有功能权限但缺资源资格
- **WHEN** 成员具有 history.read 或 todo.read，但目标记录属于其无权访问的其他用户
- **THEN** 系统拒绝操作，功能权限不能扩大私有历史、待办或其他资源的可见范围

### Requirement: 目录整理不得开放未验收消费者

功能目录、角色资源授权、模型默认值和现有分发守卫 SHALL 复用现有组件交付。目录/配置与运行状态分别标示，不以执行关闭隐藏合法目录，也不以管理完成开放执行。对已由独立运行开放规范验收的 Web 对话、文件、调度、通道、OpenAI API 与 Desktop 登录，SHALL 允许按权限真实执行。平台 all、导航或组件测试 MUST NOT 解锁未验收客户端；授权失败 MUST NOT 回退共享密码、旧 token 或匿名身份。

#### Scenario: 九项权限齐全但缺执行权
- **WHEN** `tenant_admin` 仅持有现有九项默认权限（未获显式 `chat.use`/目标 `agent.use`）并查看能力状态或直接请求聊天入口
- **THEN** 能力状态以权限不足说明，相关直接请求返回 403 类拒绝，不触发真实执行；已被显式授予执行权的成员则按开放消费者执行

#### Scenario: Desktop 使用 database 身份企业接入
- **WHEN** Desktop 以数据库账号登录并携带会话 Bearer 访问已开放的运行入口
- **THEN** 系统按该账号的租户成员与资源授权执行，不通过共享密码、旧 token 或匿名回退赋权

