# tenant-skills-tools-console Specification

## Purpose
在 database 多租户模式下，向租户内成员（持有 `agent.read`）开放对技能列表/技能内容与工具列表的受控读取，及对技能开关与技能内容写回的受限修改；skills/tools 不再作为 `closed` deferred 消费者在 database 模式返回 `503 database_unavailable`。工具列表与技能库归属租户共享根，访问按租户作用域隔离，平台管理员与无 `agent.read` 成员不在读取集合内；legacy 模式访问控制保持不变。
## Requirements
### Requirement: 技能与工具接口归类为租户域

系统 SHALL 将 `/api/tools` 的 GET、`/api/skills` 的 GET/POST、`/api/skills/content` 的 GET/POST 在路由方法策略中归类为 `tenant`（租户），MUST NOT 再作为 `closed` deferred 消费者在 database 模式返回 `503 database_unavailable`。未登录或已登录但无有效租户上下文的请求 SHALL 返回 `401`（无有效会话），MUST NOT 进入下游 handler 产生读取或写入副作用。

#### Scenario: tenant 策略可匹配
- **WHEN** 对 `/api/tools`、`/api/skills` 或 `/api/skills/content` 发起 GET/POST 请求
- **THEN** 路由策略匹配到 `tenant`，不再作为 `closed` 消费者被短路成 503

#### Scenario: 未登录访问
- **WHEN** 匿名请求访问 `/api/skills` 或 `/api/tools`
- **THEN** 系统返回 `401 unauthorized`，不产生任何技能或工具数据的读取/写入副作用

### Requirement: 租户成员可读取技能列表与工具列表

已认证有效租户成员 SHALL 凭skill.read或tool.read及相应资源read授权读取对应目录，平台all可在合法范围读取。技能管理列表按可读已安装资源过滤，含enabled、来源和可用动作；不以Agent选用列表代替管理库权限，但也不返回该用户未获准资源。工具目录返回获准工具名称、描述及来源，MCP区别连接。列表、总数和分页 SHALL 先授权过滤，不因工具执行尚未开放而关闭已验收目录。

#### Scenario: 租户成员读取技能列表
- **WHEN** 具备 skill.read 及对应资源读取授权的成员携带有效会话且选中租户 GET `/api/skills`
- **THEN** 系统返回 `200` 与获准读取的已登记技能（含名称、描述、display_name 与 `enabled` 状态）

#### Scenario: 租户成员读取工具列表
- **WHEN** 具备 tool.read 及对应资源读取授权的成员携带有效会话且选中租户 GET `/api/tools`
- **THEN** 系统返回 `200` 与获准已注册工具的名称与描述列表

### Requirement: 租户成员可读取与写回技能内容

技能正文读取 SHALL 要求skill.read与该资源read授权，写回 SHALL 要求skill.edit与该资源edit授权，平台all也须满足资源真实范围和可编辑状态；agent.read不再授予维护。接口 SHALL 增加resource_id定位并在列表返回该标识；name仅兼容在当前租户合法来源范围可唯一解析的对象，有歧义返回400并要求resource_id。授权和实际文件读写必须使用同一已解析对象，不按覆盖顺序回退。GET保持原始文本和editable投影，POST保留content、expected_mtime及版本冲突反馈。写回 SHALL 仅作用于原合法租户资源，保持源文件/存储合同，不进行显示语言改写、不越租户根或通过同名资源回退。

#### Scenario: 读取技能内容
- **WHEN** 具备 skill.read 及资源read授权的成员 GET `/api/skills/content` 并携带 `resource_id` 或可唯一解析的 `name`
- **THEN** 系统返回 `200` 与技能原始文本、`editable` 状态及来源，不进行按显示语言的简繁改写

#### Scenario: 写回技能内容
- **WHEN** 具备 skill.edit 及资源edit授权的成员 POST `/api/skills/content` 提交 `{resource_id, content, expected_mtime}` 或可唯一解析的兼容name请求
- **THEN** 系统将内容保存到租户共享根技能文件并返回成功（含写入体积），仅修改原目标技能内容，不因保存正文改写启用配置或其他资源

#### Scenario: 并发编辑冲突
- **WHEN** 提交的 `expected_mtime` 与当前文件修改时间不一致
- **THEN** 系统返回 `code:"conflict"` 与冲突说明，不覆盖当前文件

#### Scenario: 同名技能要求明确来源
- **WHEN** 内置和租户技能同名且旧请求仅带name，或请求指定resource_id
- **THEN** 歧义name请求返回400不执行；resource_id请求仅鉴权和操作对应来源，不借同名技能授权读写另一文件

### Requirement: 租户成员可切换技能启用状态

技能启停 SHALL 要求skill.enable与该资源enable授权，平台all亦受真实资源范围约束，agent.read不得赋予启停。POST沿用action:"open"|"close"及原状态配置，通过resource_id或可唯一解析的兼容name定位同一授权对象，未知action返回错误且不写入；只有当前授权及资源有效时才提交。启停后后续列表/装配/实际使用 SHALL 重新验证状态，不以启用代替成员使用授权。

#### Scenario: 启用技能
- **WHEN** 具备 skill.enable 及资源enable授权的成员 POST `/api/skills` 提交 `{action:"open", resource_id:<skill-id>}`
- **THEN** 系统将该技能标记为启用并返回 `{"status":"success"}`

#### Scenario: 禁用技能
- **WHEN** 具备 skill.enable 及资源enable授权的成员 POST `/api/skills` 提交 `{action:"close", resource_id:<skill-id>}`
- **THEN** 系统将该技能标记为禁用并返回 `{"status":"success"}`

#### Scenario: 未知 action 被拒绝
- **WHEN** POST `/api/skills` 提交无法识别的 `action`
- **THEN** 系统返回 `{"status":"error","message":"unknown action"}`，不改变任何技能状态

### Requirement: legacy 模式访问控制保持不变

当 `identity_mode=legacy`（非 database）时，`/api/skills`、`/api/tools`、`/api/skills/content` 的访问控制 SHALL 保持既有行为，继续使用共享控制台密码鉴权（`_require_auth()`），MUST NOT 引入租户 `agent.read` 权限要求或改变旧客户端行为。

#### Scenario: legacy 模式继续用共享密码鉴权
- **WHEN** 在 legacy 模式下已通过共享控制台密码认证的客户端访问 `/api/skills` 或 `/api/tools`
- **THEN** 系统行为与改动前一致，不额外要求 `agent.read` 权限，也不返回数据库模式专属的 503

### Requirement: 缺少对应技能工具授权的成员被拒绝

已登录请求 SHALL 按目标用途验证对应skill或tool功能权限与资源授权；缺动作权限返回403，不可见资源按原404规则，不执行写入或返回未授权数据。平台all同样受真实租户/资源及执行条件约束；仅有agent.read不能替代技能工具动作。授权拒绝 SHALL 正确返回HTTP错误，不被兜底异常吞为成功或普通空数据。

#### Scenario: 无权限成员读取被拒
- **WHEN** 已登录但当前租户上下文缺少相应读取功能权限的成员 GET `/api/skills` 或 `/api/tools`
- **THEN** 系统返回 `403 forbidden`，不返回任何技能或工具数据

#### Scenario: 无权限成员写回被拒
- **WHEN** 已登录但当前租户上下文缺少相应编辑/启停及资源授权的成员 POST `/api/skills` 或 `/api/skills/content`
- **THEN** 系统返回 `403 forbidden`，不产生任何技能状态切换或内容写入

