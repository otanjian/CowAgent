## REMOVED Requirements

### Requirement: 本期目录整理不得开放延期消费者

**Reason**: 该要求将 Desktop 企业登录列为「未适配、保持关闭」并允许以 legacy 作为回退表述；Desktop 已适配 database 身份，且 legacy 已删除。

**Migration**: 改由新增 Requirement「目录整理不得开放未验收消费者」承载，Desktop 按已适配 database 身份运行。

## ADDED Requirements

### Requirement: 目录整理不得开放未验收消费者

功能目录、角色资源授权、模型默认值和现有分发守卫 SHALL 复用现有组件交付。目录/配置与运行状态分别标示，不以执行关闭隐藏合法目录，也不以管理完成开放执行。对已由独立运行开放规范验收的 Web 对话、文件、调度、通道、OpenAI API 与 Desktop 登录，SHALL 允许按权限真实执行。平台 all、导航或组件测试 MUST NOT 解锁未验收客户端；授权失败 MUST NOT 回退共享密码、旧 token 或匿名身份。

#### Scenario: 九项权限齐全但缺执行权
- **WHEN** `tenant_admin` 仅持有现有九项默认权限（未获显式 `chat.use`/目标 `agent.use`）并查看能力状态或直接请求聊天入口
- **THEN** 能力状态以权限不足说明，相关直接请求返回 403 类拒绝，不触发真实执行；已被显式授予执行权的成员则按开放消费者执行

#### Scenario: Desktop 使用 database 身份企业接入
- **WHEN** Desktop 以数据库账号登录并携带会话 Bearer 访问已开放的运行入口
- **THEN** 系统按该账号的租户成员与资源授权执行，不通过共享密码、旧 token 或匿名回退赋权
