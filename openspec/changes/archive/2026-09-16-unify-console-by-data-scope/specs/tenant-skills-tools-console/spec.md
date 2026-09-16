## MODIFIED Requirements

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
