## Purpose
为智能体提供显式保存长期记忆的工具（`memory_add`），使其能按 `shared`/`user`/`session` 作用域将事实、经验与上下文写入记忆库，作为既有 `memory_search`/`memory_get` 的写入侧补充，供后续检索使用。

## ADDED Requirements

### Requirement: 提供显式记忆写入工具

系统 SHALL 提供工具 `memory_add`，使智能体能显式保存内容到长期记忆。工具 SHALL 接受必填 `content`（去空白后非空）与可选 `scope`（`shared`/`user`/`session`，默认 `user`）、可选 `path`（相对记忆路径，省略时自动生成）。工具 SHALL 复用既有记忆管理与 embedding 管线，MUST NOT 新增独立存储结构或权限目录。

#### Scenario: 保存用户作用域记忆
- **WHEN** 智能体调用 `memory_add` 提交合法 `content`（默认 `scope=user`）
- **THEN** 系统将内容写入当前用户作用域的记忆库并返回成功确认，后续可通过 `memory_search` 检索

#### Scenario: 保存共享作用域记忆
- **WHEN** 智能体调用 `memory_add` 提交合法 `content` 且 `scope=shared`
- **THEN** 系统将内容写入共享作用域记忆库并返回成功确认

#### Scenario: 内容去空白后为空被拒绝
- **WHEN** `content` 缺失或仅为空白字符
- **THEN** 工具返回失败并提示 `content` 参数必填，不写入任何记忆

#### Scenario: 非法 scope 被拒绝
- **WHEN** `scope` 取值不在 `shared`/`user`/`session` 之内
- **THEN** 工具返回失败并提示合法取值，不写入任何记忆

### Requirement: 记忆写入作用于当前用户

当 `scope=user` 时，系统 SHALL 将记忆绑定到当前请求主体对应的用户，MUST NOT 写入其它用户作用域或泄漏到非授权上下文；`scope=shared` 与 `scope=session` 按既有记忆作用域语义处理，MUST NOT 越权写入。

#### Scenario: user 作用域绑定当前用户
- **WHEN** 智能体以 `scope=user` 且工具持有 `user_id` 调用 `memory_add`
- **THEN** 记忆按该 `user_id` 写入用户作用域，返回确认中标注当前用户

#### Scenario: 无 user_id 时 user 作用域
- **WHEN** `scope=user` 但工具未持有 `user_id`
- **THEN** 系统按用户作用域语义写入，不伪造或泄漏其它用户标识

### Requirement: 写入成功反馈与失败兜底

成功写入时，系统 SHALL 返回含 `scope`（与 user 作用域的 `user` 标识）的成功反馈；写入或校验失败时 SHALL 返回明确错误信息，MUST NOT 静默成功或伪造成功状态。

#### Scenario: 成功反馈含作用域
- **WHEN** `memory_add` 成功写入记忆
- **THEN** 返回的成功消息包含实际 `scope` 及（`user` 作用域的）用户标识，并说明内容可经后续检索获取

#### Scenario: 写入异常返回错误
- **WHEN** 记忆存储或 embedding 写入过程中抛出异常
- **THEN** 工具返回失败信息，不声明写入成功，不影响既有记忆检索能力
