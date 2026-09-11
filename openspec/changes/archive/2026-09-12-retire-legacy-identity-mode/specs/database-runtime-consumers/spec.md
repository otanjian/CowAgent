## REMOVED Requirements

### Requirement: 启动通道不再限制为仅 web

**Reason**: 该要求以「legacy 启动行为不变」作为场景之一，且未要求实例显式登记；`legacy` 模式删除后需改为按显式实例登记启动。

**Migration**: 改由新增 Requirement「通道按显式实例启动且不限为仅 web」承载。

### Requirement: Desktop 企业未适配项保持关闭

**Reason**: Desktop 已适配 database 身份：登录改用数据库账号会话并以同一会话值作 Bearer，不再依赖共享密码或旧 token，因此「Desktop 企业未适配、保持关闭」不再成立。

**Migration**: Desktop 按新增 Requirement「Desktop 按 database 身份适配」运行；旧 `cow_auth_token` 客户端必须迁移到数据库登录。

## ADDED Requirements

### Requirement: 通道按显式实例启动且不限为仅 web

系统 SHALL 按配置与显式渠道实例记录解析并启动已启用的外部 IM 通道，MUST NOT 在启动阶段将通道列表强制过滤为仅 `web`。单通道启动失败 SHALL 记录错误且不得阻止 Web 控制台启动。通道实例 MUST 为显式登记记录，MUST NOT 由旧 `channel_type` 或单租户配置隐式合成。

#### Scenario: 配置含飞书实例时启动
- **WHEN** 部署的配置或实例记录包含已启用的飞书（或其他 IM）通道
- **THEN** 进程尝试启动该通道；失败时记录错误，Web 控制台仍可用

#### Scenario: 未显式登记实例
- **WHEN** 配置只有旧 `channel_type` 字段而没有显式渠道实例记录
- **THEN** 系统不隐式合成实例，不启动该通道

### Requirement: Desktop 按 database 身份适配

Desktop SHALL 通过数据库登录取得独立 AuthSession，并以同一会话值作为 `Authorization: Bearer` 访问业务接口，MUST NOT 继续使用共享密码或旧 `cow_auth_token`。系统 MUST NOT 对 Desktop 开放匿名或免登录入口；旧认证客户端 SHALL 被明确拒绝并提示需要重新登录，MUST NOT 回退 legacy 赋权。

#### Scenario: Desktop 使用数据库账号登录
- **WHEN** Desktop 用户以有效账号通过登录接口认证并携带会话 Bearer 请求业务接口
- **THEN** 系统按该会话解析身份与租户并正常提供服务

#### Scenario: Desktop 旧认证连接
- **WHEN** Desktop 使用共享密码或旧 token 连接服务
- **THEN** 系统拒绝或明确提示需要重新登录，不回退 legacy 赋权
