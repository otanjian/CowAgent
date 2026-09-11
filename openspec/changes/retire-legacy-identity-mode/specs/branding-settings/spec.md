## REMOVED Requirements

### Requirement: 当前实例管理权限

**Reason**: 该要求以共享密码与免登录作为品牌写入授权来源；`legacy` 删除后品牌写入必须仅由平台管理员与 `branding.manage` 授权。

**Migration**: 改由新增 Requirement「当前实例管理权限仅平台管理员」承载。

### Requirement: 默认可编辑与企业启用门槛

**Reason**: 该要求允许「单实例操作者登录后」与共享密码通过品牌门槛；`legacy` 删除后不再存在单实例与共享密码路径。

**Migration**: 改由新增 Requirement「默认可编辑且企业启用门槛仅平台授权」承载。

## ADDED Requirements

### Requirement: 当前实例管理权限仅平台管理员

系统 MUST 对品牌保存、Logo 替换及恢复全部默认设置执行服务端鉴权。品牌写入 SHALL 仅允许当前有效平台管理员，并在每次请求验证平台身份与 `branding.manage` 权限；MUST NOT 以共享密码、免登录或只读界面状态作为写入授权。客户端只读状态 MUST 不构成唯一保护，直接调用管理写接口同样 MUST 被拒绝。Cookie 认证的品牌写请求 MUST 校验同源来源并执行绑定当前会话的 CSRF 防护；Bearer 认证 MUST 通过受验证的 Authorization 请求头并真实通过认证。品牌写请求 MUST 拒绝仅以查询参数 token 提供认证。

#### Scenario: 平台管理员管理品牌
- **WHEN** 当前请求由有效平台管理员持 `branding.manage` 发起且通过来源与 CSRF 校验
- **THEN** 操作者可读取编辑表单，并在其他校验通过后保存或恢复品牌

#### Scenario: 非平台管理员写入
- **WHEN** 非平台管理员或缺少 `branding.manage` 的账号提交品牌写请求
- **THEN** 系统返回 403，品牌元数据、图片与发布版本均不改变

#### Scenario: 未登录直接修改
- **WHEN** 实例收到未认证或认证无效的品牌写请求
- **THEN** 系统返回 401，且品牌元数据、图片与发布版本均不改变

#### Scenario: 拒绝跨站 Cookie 写请求
- **WHEN** 请求携带有效登录 Cookie，但来源非同源或未通过绑定当前会话的 CSRF 校验
- **THEN** 系统拒绝保存或恢复请求，不改变任何已发布品牌内容

#### Scenario: query token 不能授权品牌写入
- **WHEN** 请求仅在 URL 查询参数中携带 token，并尝试保存或恢复品牌
- **THEN** 系统拒绝该写请求，不将该 token 视为品牌管理的有效写入认证

### Requirement: 默认可编辑且企业启用门槛仅平台授权

系统 SHALL 默认提供品牌设置，不要求额外启用；旧配置中的 `branding_enabled` MUST 被忽略，页面 MUST 不显示「品牌功能未开启」提示，也不得因此禁用表单。已保存品牌 SHALL 直接展示。品牌发布 MUST 具备可信的平台主体解析和 `branding.manage` 权限逐请求校验，并 MUST 以内部审计能力（`audit-log`）的切片验收为前置；共享密码或免登录 MUST NOT 代替该授权。门槛缺失时 MUST 禁止企业品牌发布，不以界面可用、接口占位或模拟验证认定门槛通过。

#### Scenario: 平台管理员直接编辑
- **WHEN** 有效平台管理员打开品牌设置，且实例旧配置中开关为 false
- **THEN** 系统允许编辑品牌名称、Logo 和描述并保存，不显示未开启提示

#### Scenario: 重启后直接读取已保存品牌
- **WHEN** 已保存品牌的实例重启且满足当前部署的前置条件
- **THEN** 系统读取先前保存的品牌，不要求重复上传或覆盖默认资源

#### Scenario: 管理权限被收回
- **WHEN** 操作者在打开页面后平台 `branding.manage` 权限被撤销，再提交品牌变更
- **THEN** 服务端按本次请求的有效权限拒绝发布，即使页面仍保留编辑状态

#### Scenario: 企业部署前置未完成
- **WHEN** 企业生产部署缺少真实审计验收证据，或缺少可信平台授权能力
- **THEN** 系统禁止品牌发布，不能通过客户端参数、查询参数 token 或模拟依赖绕过门槛
