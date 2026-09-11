# agent-memory-explicit-add-tool Specification

## Purpose
为智能体提供显式保存长期记忆的工具（`memory_add`），使其能按 `shared`/`user`/`session` 作用域将事实、经验与上下文写入记忆库，作为既有 `memory_search`/`memory_get` 的写入侧补充，供后续检索使用。
## Requirements
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

当 `scope=user` 时，系统 SHALL 将记忆绑定到当前请求主体对应的用户，MUST NOT 写入其它用户作用域或泄漏到非授权上下文；`scope=shared` 与 `scope=session` 按既有记忆作用域语义处理，MUST NOT 越权写入。该用户的绑定 SHALL 来源于已验证的运行时身份，MUST NOT 以调用方提供的用户标识决定归属；同一用户经不同智能体写入的个人记忆 SHALL 共享同一归属，并在该用户有权使用的任意智能体下一致可见。

#### Scenario: user 作用域绑定当前用户
- **WHEN** 智能体以 `scope=user` 调用 `memory_add`，且当前运行时身份带有已验证的 `user_id`
- **THEN** 记忆按该 `user_id` 写入用户作用域，返回确认中标注当前用户

#### Scenario: 无 user_id 时 user 作用域
- **WHEN** `scope=user` 但当前运行时身份未持有 `user_id`
- **THEN** 系统按用户作用域语义写入，不伪造或泄漏其它用户标识

#### Scenario: 拒绝调用方指定的用户标识
- **WHEN** 调用方在参数中携带与已验证运行时身份不一致的用户标识
- **THEN** 系统按已验证身份归属写入，MUST NOT 以调用方标识覆盖，也不向其指定的用户作用域写入

#### Scenario: 跨智能体归属一致
- **WHEN** 同一用户先后经两个不同的智能体以 `scope=user` 写入并检索记忆
- **THEN** 两次写入共享同一用户归属，第二个智能体可检索到第一个智能体写入的本人记忆

#### Scenario: 不越权覆盖共享域
- **WHEN** 调用方以 `scope=user` 提交内容
- **THEN** 内容 MUST NOT 落入 `scope=shared`，MUST NOT 覆盖智能体共享记忆文件

### Requirement: 写入成功反馈与失败兜底

成功写入时，系统 SHALL 返回含 `scope`（与 user 作用域的 `user` 标识）的成功反馈；写入或校验失败时 SHALL 返回明确错误信息，MUST NOT 静默成功或伪造成功状态。

#### Scenario: 成功反馈含作用域
- **WHEN** `memory_add` 成功写入记忆
- **THEN** 返回的成功消息包含实际 `scope` 及（`user` 作用域的）用户标识，并说明内容可经后续检索获取

#### Scenario: 写入异常返回错误
- **WHEN** 记忆存储或 embedding 写入过程中抛出异常
- **THEN** 工具返回失败信息，不声明写入成功，不影响既有记忆检索能力

