## Purpose

为 CowAgent 已有九项功能权限补齐统一元数据、内置显式默认集合与当前租户的本人有效能力查询，保持身份管理资格和业务访问范围的边界。本次仅完善现有身份安全及管理页面，不预登记未来执行权限，也不借目录调整开放尚未适配的业务或客户端。

## ADDED Requirements

### Requirement: 权限目录有限且具有稳定元数据

系统本次 SHALL 仅为 tenant.info.read、tenant.members.read、tenant.org.read、agent.read、history.read、knowledge.read、memory.read、todo.read、todo.write 九个既有权限 ID 提供稳定 id、group、label、description、scope 和 assignable 元数据。自定义角色 SHALL 只能选择目录允许分配的权限；未知 ID、通配符、平台标识或身份管理员资格不得保存为业务授权。本次 MUST NOT 预登记聊天执行、资源 grants、模型策略、SSO 或 Desktop 企业适配的未来权限，不按名称前缀推断授权。

#### Scenario: 展示并选择登记权限
- **WHEN** 租户管理员为自定义角色查看和选择可分配权限
- **THEN** 服务端返回分组、用途及作用域明确的目录，保存后仅授予所选择且允许分配的稳定权限 ID

#### Scenario: 提交未知或不可分配权限
- **WHEN** 角色写请求包含 chat.use 等未登记 ID、通配符、平台管理员标识或身份审计权限
- **THEN** 系统拒绝整次变更，不保存部分权限或将输入解释为隐式管理员资格

### Requirement: 内置角色采用显式默认权限且升级不自动扩权

member 与 tenant_admin 的业务权限 SHALL 使用显式默认集合：member 保留 tenant.info.read、agent.read、history.read、knowledge.read、memory.read、todo.read、todo.write；tenant_admin 保留上述九个既有权限。业务请求 MUST 根据实际有效权限及资源范围检查，不以通用 tenant_admin 短路替代业务检查，也不把完整目录自动视为管理员授权。既有自定义角色保持原权限，未知或未来权限不得因管理员资格取得允许结果。

#### Scenario: 升级保留既有角色权限
- **WHEN** 已有 tenant_admin、member 和自定义角色用户在本次目录调整后再次请求有效权限
- **THEN** 内置角色分别取得明确的九项和七项权限，自定义角色保持原选择，不出现新增执行权，既有读取及待办继续遵守原资源范围

#### Scenario: 管理员不能短路未登记业务权限
- **WHEN** tenant_admin 尝试请求未登记或未开放的业务执行能力
- **THEN** 请求按实际路由开放状态及权限检查被拒绝，不因管理员名称、九项权限或资源可读而放行

### Requirement: 平台控制面与租户身份管理资格独立

平台管理员 SHALL 仅因该资格取得全局账号与租户生命周期等明确平台管理能力，不自动取得租户 Membership 或业务资源访问权。当前租户身份写入 SHALL 由有效 tenant_admin 资格控制，不向普通角色开放任意身份授权委派；普通角色权限并集、部门或岗位不得产生该资格。未通过本期适配的全局供应商凭据、MCP、通道或系统配置业务入口 SHALL 保持原有关闭边界，不能通过给 tenant_admin 增加业务权限开放全局写入。

#### Scenario: 平台管理员无业务成员资格
- **WHEN** 未加入目标租户的平台管理员请求该租户聊天、成员写入或私有资源
- **THEN** 系统拒绝访问，平台管理资格不能充当目标租户成员、身份管理员或业务授权

#### Scenario: 自定义角色请求身份或全局配置管理
- **WHEN** 普通成员持有多个自定义业务权限并尝试授予身份管理员资格，或租户管理员请求尚未租户化的全局配置写入
- **THEN** 系统依据身份域和功能启用状态拒绝，角色权限数量及 tenant_admin 身份不能越过该边界

### Requirement: 有效权限上下文只补当前租户展示能力

GET /auth/context SHALL 根据有效会话及 X-Tenant-ID 验证本人在所选租户的有效成员资格，仅返回 effective_permissions、当前管理资格、消费者可用状态及不可用原因，不返回租户名称、编码、目录、其他成员或平台租户资料。该接口 MUST NOT 要求 tenant.info.read，零业务权限的有效成员仍可读取本人能力；缺少选择返回 400，无有效成员资格返回 403，身份服务故障返回 503，受限改密会话不得读取租户能力。/auth/me SHALL 保持既有个人及所有有效租户摘要契约，不增加所有租户权限计算。Web 仅用此上下文展示能力，后续请求 MUST 独立重新授权；Desktop 企业能力仅标示未适配，本次不实现其账号或传输适配。

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

有效成员的业务功能权限 SHALL 取当前租户有效角色的显式权限并集，并在每次业务请求重新验证；撤销角色或权限最迟在下一请求生效，其他角色仍提供相同权限时继续保留该权限。功能权限 SHALL 同时受现有业务资源归属和开放状态约束，不能替代 owner、私有数据范围或既有隔离检查；部门变化不隐式授权。本次不增加资源 grant、模型选择策略或执行授权模型。

#### Scenario: 撤销一个重复权限来源
- **WHEN** 成员两个角色均授予同一权限，其中一个角色被撤销
- **THEN** 当前权限仍可由剩余角色提供；撤销最后有效来源后下一请求被拒绝，旧能力上下文不能延长授权

#### Scenario: 有功能权限但缺资源资格
- **WHEN** 成员具有 history.read 或 todo.read，但目标记录属于其无权访问的其他用户
- **THEN** 系统拒绝操作，功能权限不能扩大私有历史、待办或其他资源的可见范围

### Requirement: 本期目录整理不得开放延期消费者

本次身份与管理页面改造 SHALL 保留既有受控读取及待办能力的已验收边界。资源授权、模型策略、聊天运行、文件、调度、OpenAI API、外部通道、SSO 和 Desktop 企业适配 SHALL 明确延期，未适配入口继续关闭并返回可用的关闭原因；/auth/context 不得将其标示为可用。目录元数据、管理员资格、已有配置开关或旧客户端行为 MUST NOT 单独开放这些消费者，database 失败不得回退 legacy。新 CLI 账号管理同样不计入本期交付。

#### Scenario: 九项权限齐全但运行仍延期
- **WHEN** tenant_admin 持有现有九项权限并查看能力状态或直接请求尚未适配的聊天、文件等入口
- **THEN** 能力状态显示未开放及原因，相关直接请求返回 503，不触发真实执行

#### Scenario: Desktop 仍未适配企业身份
- **WHEN** 当前 Desktop 连接 database 服务并尝试使用其旧认证或运行入口
- **THEN** 系统不通过共享密码、旧 token 或 legacy 回退赋权，明确显示企业接入未开放，不把本期 Web 管理完成当作 Desktop 可用证明
