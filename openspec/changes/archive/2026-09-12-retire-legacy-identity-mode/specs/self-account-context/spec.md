## REMOVED Requirements

### Requirement: 单一本人接口由有效会话确定主体

**Reason**: 该要求包含「legacy 模式 SHALL 明确拒绝该数据库个人资料能力」及「legacy 模式请求本人资料」场景；legacy 删除后本人接口只有一种身份模式。

**Migration**: 改由新增 Requirement「本人接口由有效会话确定主体」承载。

## ADDED Requirements

### Requirement: 本人接口由有效会话确定主体

系统 SHALL 提供 `GET /auth/me`，从当前有效会话确定本人，无需平台管理员、租户管理员或用户管理权限。正常成功响应 SHALL 为 `status: "success"`、`user: {id, username, display_name, is_platform_admin}`、布尔值 `must_change_password` 及 `tenants: [{id, code, name, membership: {display_name, roles: [{code, name}], department: {id, name} | null, position_text}}]`。接口 SHALL 不依赖 `X-Tenant-ID`，不允许客户端通过用户或成员标识改选读取主体；没有有效租户时返回空列表，附带陈旧租户头不影响全局身份读取。系统 MUST NOT 构造虚假用户、回退免登录身份或保留共享密码语义。

#### Scenario: 普通成员一次读取本人资料
- **WHEN** 无用户管理权限的普通成员持有效且非受限会话请求 `/auth/me`
- **THEN** 接口返回规定的全局账号及本人有效租户成员摘要，前端按当前租户 ID 选取一条展示，无需另请求租户详情

#### Scenario: 客户端指定他人身份
- **WHEN** 请求通过参数或请求头指定另一用户或成员
- **THEN** 接口拒绝该选择或仍仅依据当前会话读取本人，不返回他人资料

#### Scenario: 缺少或失效的租户选择
- **WHEN** 有效用户未选择租户、附带陈旧租户头，或已无任何有效租户
- **THEN** 接口仍正常返回本人全局资料与最新有效列表，无有效租户时返回 `tenants: []`，不将租户选择问题视为退出登录

#### Scenario: 未认证请求
- **WHEN** 未携带有效会话的客户端请求 `/auth/me`
- **THEN** 接口返回 401，不返回任何账号或组织资料
