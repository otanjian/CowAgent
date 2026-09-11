## REMOVED Requirements

### Requirement: 身份模式与独立登录会话

**Reason**: 系统不再区分 `legacy` 与 `database` 身份模式，`legacy` 认证接口、共享密码与旧 HMAC token 全部删除，本要求中「显式区分双模式」「legacy 保留既有认证接口」「未迁移实例以 legacy 运行」等内容不再成立。

**Migration**: 身份模式唯一化与独立登录会话改由新增 Requirement「身份模式唯一与独立登录会话」承载；首启可用性与旧模式拒绝启动见 `database-bootstrap`。

### Requirement: 首期消费者关闭且不得绕过认证

**Reason**: 运行消费者的开放边界已由 `database-runtime-consumers` 完整定义并已验收开放，本要求中「首期仅开放 Web 身份管理与受控读取、关闭 SSE/poll/运行控制」以及「现有 legacy 消费者保持兼容」的表述与现状冲突且不再成立。

**Migration**: 消费者开放与逐请求重新授权改由 `database-runtime-consumers` 承载；不得回退旧认证改由本规范新增 Requirement「消费者开放不得绕过认证」承载。

## ADDED Requirements

### Requirement: 身份模式唯一与独立登录会话

系统 SHALL 仅以数据库身份模式运行，使用全局账号密码与独立 AuthSession；MUST NOT 提供 `legacy` 模式，MUST NOT 保留共享密码或旧 HMAC 认证接口。会话凭据 SHALL 是高熵、不透明、可到期和撤销的随机值，数据库仅保存摘要、用户及必要会话状态，不保存当前租户或权限快照。业务 session、Agent ID、客户端自报身份、共享密码和旧 HMAC token MUST NOT 作为认证凭据。`/auth/login/check/logout` SHALL 保留原兼容字段并增量提供账号及有效租户信息；Web 使用 HttpOnly、SameSite Cookie，HTTPS 使用 Secure，Web 兼容 token 字段为空且不向脚本交付可重用会话令牌。Desktop 等程序化客户端 SHALL 通过受验证的 `Authorization: Bearer` 携带同一会话值访问，MUST NOT 继续使用旧 `cow_auth_token` 或 URL 查询参数 token。身份库不可用 SHALL 返回 503，不得回退共享密码、免登录或匿名身份。

#### Scenario: Web 使用数据库登录
- **WHEN** 有效账号完成认证
- **THEN** 系统设置受保护 Cookie 并返回兼容认证字段及本人身份信息，后续登录状态独立于聊天 session，响应不包含可重用令牌或密码摘要

#### Scenario: 程序化客户端使用 Bearer
- **WHEN** Desktop 或程序化客户端通过登录接口取得会话，并以同一会话值作为 Bearer 访问业务接口
- **THEN** 系统按该会话解析身份并授权，不要求也不接受旧共享密码 token

#### Scenario: 拒绝旧凭据和故障回退
- **WHEN** 请求携带旧共享密码、旧 HMAC token、URL 查询参数 token 或仅有客户端身份声明，或无法读取身份库
- **THEN** 旧凭据和声明不能通过认证；身份库故障返回 503，系统不开放旧认证路径或业务数据

### Requirement: 消费者开放不得绕过认证

系统 SHALL 对已开放的运行消费者逐请求验证身份与授权，MUST NOT 因已有 Cookie、Bearer 或查询参数 token 放行未授权能力，MUST NOT 恢复 URL 长效令牌认证或借用人工 Membership，MUST NOT 提供免登录或匿名运行入口。

#### Scenario: 未授权访问运行入口
- **WHEN** 已认证但无相应权限的用户调用已开放的运行入口
- **THEN** 服务端按身份与授权拒绝，不因已有 Cookie 或 Bearer 放行

#### Scenario: 后台消费者身份缺失
- **WHEN** 后台线程或定时触发丢失身份上下文后尝试执行消费者动作
- **THEN** 系统拒绝执行并记录可诊断告警，不降级为匿名或服务身份
