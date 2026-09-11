## Purpose

定义外部 IM 身份与本地 `User` 的管理员预绑定、入站解析与未绑定拒绝策略，使 database 模式下通道消息始终以真实用户权限执行，禁止以通道服务进程身份冒充成员。

## ADDED Requirements

### Requirement: 外部身份绑定存储唯一且可审计

系统 SHALL 在 `identity.db` 持久化 `external_identities`，以 `(provider, issuer_or_corp_id, subject)` 唯一映射到一个本地 `User`，并记录创建时间与最近使用时间（若实现）。同一外部三元组 MUST NOT 绑定多个用户；重复绑定 SHALL 返回冲突错误且不覆盖既有绑定。解绑 SHALL 立即生效于后续入站解析。

#### Scenario: 管理员绑定飞书用户
- **WHEN** 具备管理权的操作者为某 User 绑定 `provider=feishu`、企业标识与 `open_id`/`subject`
- **THEN** 绑定持久化成功；再次将同一三元组绑到另一 User 时被拒绝

#### Scenario: 解绑后立即失效
- **WHEN** 管理员解除某外部身份绑定后，该 IM 用户再次发来消息
- **THEN** 系统按未绑定处理，不使用已删除映射执行 Agent

### Requirement: 仅管理员可预绑定

创建、列出与删除外部身份绑定 SHALL 仅允许平台管理员或具备相应租户管理资格的管理员通过管理 API/控制台完成。系统 MUST NOT 提供用户自助确认绑定或凭昵称自动匹配建绑。展示名/昵称 MUST NOT 作为绑定匹配键。

#### Scenario: 普通成员尝试自助绑定
- **WHEN** 非管理员调用绑定写入接口
- **THEN** 系统返回 `403`，不创建绑定行

### Requirement: 入站消息必须解析到有效成员

外部 IM 通道在 database 模式下处理入站消息时，SHALL 先解析 `provider`、issuer/corp 与 subject，查找绑定用户，并验证用户 active、目标租户成员 active。通过后 SHALL 以该用户的当前权限并集（含 `chat.use`、目标 `agent.use`、`model.use` 及技能/工具等）执行，MUST NOT 使用通道服务账号、平台进程或 Agent 作者身份伪造 Membership。

#### Scenario: 已绑定且获权用户发消息
- **WHEN** 已绑定用户在有效租户成员下向已启用通道发消息，并持有执行所需权限与资源 grant
- **THEN** 系统以该用户身份进入 Agent 执行路径

#### Scenario: 未绑定用户发消息
- **WHEN** 外部身份在库中无绑定记录
- **THEN** 系统不调用模型，向会话返回固定未绑定提示，并记录可审计事件（不含敏感 token）

#### Scenario: 已绑定但成员停用或无权
- **WHEN** 外部身份已绑定，但用户/成员停用，或缺少 `chat.use`/目标 `agent.use`/所需 `model.use`
- **THEN** 系统拒绝执行，返回固定拒绝提示，不降级为匿名或服务身份执行

### Requirement: 通道 Agent 绑定不替代用户身份

通道实例仍可绑定默认 Agent 用于路由目标工作区，但执行时的授权主体 SHALL 始终为映射用户。缺少用户映射时 MUST NOT 回退为「仅按 Agent 绑定的租户服务身份」执行对话。

#### Scenario: 通道已绑 Agent 但用户未映射
- **WHEN** 通道实例已配置 `agent_id`，入站用户未绑定
- **THEN** 仍拒绝执行，不因 Agent 绑定而代跑
