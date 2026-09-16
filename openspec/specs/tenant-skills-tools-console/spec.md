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

技能正文读取 SHALL 要求skill.read与该资源read授权，写回 SHALL 要求skill.edit与该资源edit授权；公共定义写入另须对应管理资格，本人资源或本人使用配置按同一对象范围校验，平台all也须满足资源真实范围和可编辑状态；agent.read不再授予维护。接口 SHALL 增加resource_id定位并在列表返回该标识；name仅兼容在当前租户合法来源范围可唯一解析的对象，有歧义返回400并要求resource_id。授权和实际文件读写必须使用同一已解析对象，不按覆盖顺序回退。GET保持原始文本和editable投影，POST保留content、expected_mtime及版本冲突反馈。写回 SHALL 仅作用于原合法租户资源，保持源文件/存储合同，不进行显示语言改写、不越租户根或通过同名资源回退。

#### Scenario: 读取技能内容
- **WHEN** 具备 skill.read 及资源read授权的成员 GET `/api/skills/content` 并携带 `resource_id` 或可唯一解析的 `name`
- **THEN** 系统返回 `200` 与技能原始文本、`editable` 状态及来源，不进行按显示语言的简繁改写

#### Scenario: 写回技能内容
- **WHEN** 具备对应管理资格、skill.edit 及资源edit授权的管理员 POST `/api/skills/content` 提交 `{resource_id, content, expected_mtime}` 或可唯一解析的兼容name请求
- **THEN** 系统将内容保存到租户共享根技能文件并返回成功（含写入体积），仅修改原目标技能内容，不因保存正文改写启用配置或其他资源

#### Scenario: 并发编辑冲突
- **WHEN** 提交的 `expected_mtime` 与当前文件修改时间不一致
- **THEN** 系统返回 `code:"conflict"` 与冲突说明，不覆盖当前文件

#### Scenario: 同名技能要求明确来源
- **WHEN** 内置和租户技能同名且旧请求仅带name，或请求指定resource_id
- **THEN** 歧义name请求返回400不执行；resource_id请求仅鉴权和操作对应来源，不借同名技能授权读写另一文件

### Requirement: 租户成员可切换技能启用状态

技能启停 SHALL 要求skill.enable与该资源enable授权；公共安装/全局启停另须对应管理资格，本人选用状态按本人对象范围判断，平台all亦受真实资源范围约束，agent.read不得赋予启停。POST沿用action:"open"|"close"及原状态配置，通过resource_id或可唯一解析的兼容name定位同一授权对象，未知action返回错误且不写入；只有当前授权及资源有效时才提交。启停后后续列表/装配/实际使用 SHALL 重新验证状态，不以启用代替成员使用授权。

#### Scenario: 启用技能
- **WHEN** 具备对应管理资格、skill.enable 及资源enable授权的管理员 POST `/api/skills` 提交 `{action:"open", resource_id:<skill-id>}`
- **THEN** 系统将该技能标记为启用并返回 `{"status":"success"}`

#### Scenario: 禁用技能
- **WHEN** 具备对应管理资格、skill.enable 及资源enable授权的管理员 POST `/api/skills` 提交 `{action:"close", resource_id:<skill-id>}`
- **THEN** 系统将该技能标记为禁用并返回 `{"status":"success"}`

#### Scenario: 未知 action 被拒绝
- **WHEN** POST `/api/skills` 提交无法识别的 `action`
- **THEN** 系统返回 `{"status":"error","message":"unknown action"}`，不改变任何技能状态

### Requirement: 缺少对应技能工具授权的成员被拒绝

已登录请求 SHALL 按目标用途验证对应skill或tool功能权限与资源授权；缺动作权限返回403，不可见资源按原404规则，不执行写入或返回未授权数据。平台all同样受真实租户/资源及执行条件约束；仅有agent.read不能替代技能工具动作。授权拒绝 SHALL 正确返回HTTP错误，不被兜底异常吞为成功或普通空数据。

#### Scenario: 无权限成员读取被拒
- **WHEN** 已登录但当前租户上下文缺少相应读取功能权限的成员 GET `/api/skills` 或 `/api/tools`
- **THEN** 系统返回 `403 forbidden`，不返回任何技能或工具数据

#### Scenario: 无权限成员写回被拒
- **WHEN** 已登录但当前租户上下文缺少相应编辑/启停及资源授权的成员 POST `/api/skills` 或 `/api/skills/content`
- **THEN** 系统返回 `403 forbidden`，不产生任何技能状态切换或内容写入

### Requirement: 成员个人目录只展示已授权资源

现有工具与技能共用页面 SHALL 允许成员查看已授权工具与技能的名称、用途、来源、说明和可用状态，仍使用既有功能及资源读取授权。列表、搜索、总数和分页 SHALL 在授权过滤后计算；目录开放与执行开放 SHALL 分开，MUST NOT 返回公共或他人个人凭证。

#### Scenario: 成员查看获准目录

- **WHEN** member 获得部分工具和技能读取授权并进入现有工具与技能页面
- **THEN** 仅显示相应资源和真实状态，不能通过搜索、分页或内容接口发现其他资源

#### Scenario: 执行未开放但目录已开放

- **WHEN** 成员有目录读取资格但执行消费者关闭
- **THEN** 仍可查看获准说明，运行操作明确不可用，不将目录读取解释为运行许可

#### Scenario: 无独立个人目录
- **WHEN** 普通用户与管理员打开工具与技能
- **THEN** 共用同一正式页面及字段，列表依各自合法资源范围过滤，不提供另一套个人目录

### Requirement: 个人使用配置与公共资源维护分离

在资源支持个人参数且本人有相应个人配置能力时，成员 SHALL 能为自己的使用场景保存参数与个人授权信息，配置 SHALL 归属当前租户和用户。该动作 MUST NOT 改写工具或技能的公共定义、公共连接地址与凭证、安装状态、正文或全局启停，也 MUST NOT 新增未经授权的资源。公共维护 SHALL 同时要求对应管理资格及既有明确功能与资源动作授权，不能从私有 Agent 所有权或默认 member 资格派生。

#### Scenario: 保存个人工具参数

- **WHEN** 成员为获准且支持个人参数的工具保存配置
- **THEN** 仅本人当前租户的个人配置改变，公共配置与其他成员结果不变，敏感值仅以受控凭证保存

#### Scenario: 个人配置试图改写公共资源

- **WHEN** member 通过个人配置请求修改公共 MCP 连接、技能正文或全局启用状态
- **THEN** 请求被拒绝且无公共变更，既有公共管理接口仍独立验证其明确授权

#### Scenario: 保存个人配置后资源被撤权

- **WHEN** 个人参数已保存但成员随后失去该工具或技能的使用授权
- **THEN** 后续使用被拒绝，保存的个人参数和凭证不产生额外执行资格

参数配置 SHALL 位于正式工具与技能的同一资源详情组件，不能恢复独立个人页面、页签或业务服务。普通用户的模型目录同样只呈现获授权资源，公共模型服务地址及密钥仍由对应管理员维护；目录可见不替代执行资格。

#### Scenario: 普通用户持有公共编辑 grant
- **WHEN** 普通用户具有公共技能编辑 grant 但没有对应管理资格
- **THEN** 公共正文修改被拒绝，其本人合法参数及已授权使用仍按共用流程开放

