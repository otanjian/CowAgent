## Purpose

在 database 多租户模式下，向租户内成员（持有 `agent.read`）开放对技能列表/技能内容与工具列表的受控读取，及对技能开关与技能内容写回的受限修改；skills/tools 不再作为 `closed` deferred 消费者在 database 模式返回 `503 database_unavailable`。工具列表与技能库归属租户共享根，访问按租户作用域隔离，平台管理员与无 `agent.read` 成员不在读取集合内；legacy 模式访问控制保持不变。

## ADDED Requirements

### Requirement: 技能与工具接口归类为租户域

系统 SHALL 将 `/api/tools` 的 GET、`/api/skills` 的 GET/POST、`/api/skills/content` 的 GET/POST 在路由方法策略中归类为 `tenant`（租户），MUST NOT 再作为 `closed` deferred 消费者在 database 模式返回 `503 database_unavailable`。未登录或已登录但无有效租户上下文的请求 SHALL 返回 `401`（无有效会话），MUST NOT 进入下游 handler 产生读取或写入副作用。

#### Scenario: tenant 策略可匹配
- **WHEN** 对 `/api/tools`、`/api/skills` 或 `/api/skills/content` 发起 GET/POST 请求
- **THEN** 路由策略匹配到 `tenant`，不再作为 `closed` 消费者被短路成 503

#### Scenario: 未登录访问
- **WHEN** 匿名请求访问 `/api/skills` 或 `/api/tools`
- **THEN** 系统返回 `401 unauthorized`，不产生任何技能或工具数据的读取/写入副作用

### Requirement: 租户成员可读取技能列表与工具列表

已认证且当前租户上下文具备 `agent.read` 权限的请求 SHALL 能够读取技能列表与工具列表。GET `/api/skills` SHALL 返回 `{status:"success", skills:[...]}`，包含租户共享根下已登记的全部技能及其 `enabled` 状态；GET `/api/tools` SHALL 返回 `{status:"success", tools:[...]}`，包含已注册的内置工具名称与描述。列表读取 MUST NOT 依赖具体 Agent 绑定即可返回完整库（技能库页需展示全部已安装技能以便编辑选择）。

#### Scenario: 租户成员读取技能列表
- **WHEN** 具备 `agent.read` 权限的成员携带有效会话且选中租户 GET `/api/skills`
- **THEN** 系统返回 `200` 与全部已登记技能（含名称、描述、display_name 与 `enabled` 状态）

#### Scenario: 租户成员读取工具列表
- **WHEN** 具备 `agent.read` 权限的成员携带有效会话且选中租户 GET `/api/tools`
- **THEN** 系统返回 `200` 与已注册内置工具的名称与描述列表

### Requirement: 租户成员可读取与写回技能内容

已认证且当前租户上下文具备 `agent.read` 权限的请求 SHALL 能够按名称读取单个技能内容，并受限地写回（保存）技能内容。GET `/api/skills/content?name=<skill>` SHALL 返回该技能的原始文本（不作简繁转换，`editable` 反映是否可编辑）；POST `/api/skills/content` SHALL 接受 `{name, content, expected_mtime}` 并保存到租户共享根下的技能文件，遇并发编辑冲突时返回 `code:"conflict"`。写入 MUST 遵循租户共享根解析（经当前身份定位 `~/cow/skills`），MUST NOT 越出租户共享根。

#### Scenario: 读取技能内容
- **WHEN** 具备 `agent.read` 权限的成员 GET `/api/skills/content` 并携带 `name`
- **THEN** 系统返回 `200` 与技能原始文本、`editable` 状态及来源，不进行按显示语言的简繁改写

#### Scenario: 写回技能内容
- **WHEN** 具备 `agent.read` 权限的成员 POST `/api/skills/content` 提交 `{name, content, expected_mtime}`
- **THEN** 系统将内容保存到租户共享根技能文件并返回成功（含写入体积），不修改 `skills_config.json` 之外的其它库

#### Scenario: 并发编辑冲突
- **WHEN** 提交的 `expected_mtime` 与当前文件修改时间不一致
- **THEN** 系统返回 `code:"conflict"` 与冲突说明，不覆盖当前文件

### Requirement: 租户成员可切换技能启用状态

已认证且当前租户上下文具备 `agent.read` 权限的请求 SHALL 能够通过 POST `/api/skills` 切换单个技能的启用/禁用状态。POST SHALL 接受 `{action:"open"|"close", name:<skill>}` 并更新 `skills_config.json` 中该技能的 `enabled` 状态，返回 `{"status":"success"}`；未知 `action` SHALL 返回 `{"status":"error", "message":"unknown action"}`。

#### Scenario: 启用技能
- **WHEN** 具备 `agent.read` 权限的成员 POST `/api/skills` 提交 `{action:"open", name:<skill>}`
- **THEN** 系统将该技能标记为启用并返回 `{"status":"success"}`

#### Scenario: 禁用技能
- **WHEN** 具备 `agent.read` 权限的成员 POST `/api/skills` 提交 `{action:"close", name:<skill>}`
- **THEN** 系统将该技能标记为禁用并返回 `{"status":"success"}`

#### Scenario: 未知 action 被拒绝
- **WHEN** POST `/api/skills` 提交无法识别的 `action`
- **THEN** 系统返回 `{"status":"error","message":"unknown action"}`，不改变任何技能状态

### Requirement: 无 `agent.read` 权限的成员被拒绝

已登录但当前租户上下文不具备 `agent.read` 权限的请求 SHALL 被拒绝访问技能与工具接口，返回 `403`，MUST NOT 获取技能/工具数据或执行写回/切换。授权拒绝 MUST 从 handler 的兜底 `except` 中正确上抛，MUST NOT 被吞成 `{"status":"error"}`。

#### Scenario: 无权限成员读取被拒
- **WHEN** 已登录但当前租户上下文无 `agent.read` 权限的成员 GET `/api/skills` 或 `/api/tools`
- **THEN** 系统返回 `403 forbidden`，不返回任何技能或工具数据

#### Scenario: 无权限成员写回被拒
- **WHEN** 已登录但当前租户上下文无 `agent.read` 权限的成员 POST `/api/skills` 或 `/api/skills/content`
- **THEN** 系统返回 `403 forbidden`，不产生任何技能状态切换或内容写入

### Requirement: legacy 模式访问控制保持不变

当 `identity_mode=legacy`（非 database）时，`/api/skills`、`/api/tools`、`/api/skills/content` 的访问控制 SHALL 保持既有行为，继续使用共享控制台密码鉴权（`_require_auth()`），MUST NOT 引入租户 `agent.read` 权限要求或改变旧客户端行为。

#### Scenario: legacy 模式继续用共享密码鉴权
- **WHEN** 在 legacy 模式下已通过共享控制台密码认证的客户端访问 `/api/skills` 或 `/api/tools`
- **THEN** 系统行为与改动前一致，不额外要求 `agent.read` 权限，也不返回数据库模式专属的 503
