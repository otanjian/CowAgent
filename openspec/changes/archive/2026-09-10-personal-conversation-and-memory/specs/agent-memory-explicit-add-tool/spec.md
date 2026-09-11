## MODIFIED Requirements

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
