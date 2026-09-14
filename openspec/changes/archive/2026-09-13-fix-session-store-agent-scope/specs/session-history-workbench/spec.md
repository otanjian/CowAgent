## ADDED Requirements

### Requirement: 会话读写指向被寻址 Agent 的会话存储

在本部署中会话按 Agent 工作区独立存储（`<workspace>/memory/long-term/index.db`）。任何按会话 ID 的读取或写入（历史列表、重命名、置顶、归档、删除会话、标题回填、清空上下文、删除单条消息）SHALL 在被寻址 Agent 的会话存储上执行，MUST NOT 用请求工作区根（database 模式下的租户共享根）定位会话存储。

同一 Agent 的可读集合与可写集合 SHALL 一致：凡是历史列表能列出的会话，其重命名/置顶/归档/删除/消息操作 SHALL 命中同一存储，MUST NOT 因解析到另一数据库而回报会话不存在。

租户工作区语义 MUST 保持不变：文件面板、预览、上传与知识库等仍按租户共享根解析，本要求 MUST NOT 放宽或改变租户边界。

#### Scenario: 重命名非默认 Agent 的会话
- **WHEN** 用户重命名一条属于非默认 Agent（其会话存放在该 Agent 工作区）的会话
- **THEN** 写入在该 Agent 的会话存储上生效并返回成功，标题持久化为新值

#### Scenario: 归档非默认 Agent 的会话
- **WHEN** 用户归档一条属于非默认 Agent 的会话
- **THEN** 归档标记写入该 Agent 的会话存储，该会话从默认历史列表中消失

#### Scenario: 写路径与列表路径一致
- **WHEN** 对同一 Agent 同时取值历史列表与执行会话写入
- **THEN** 两者解析到同一个会话存储，写入的会话对列表立即可见

#### Scenario: 租户工作区解析不受影响
- **WHEN** 数据库模式下请求文件面板、预览、上传或知识库
- **THEN** 仍解析到调用者的租户共享根，不因本要求改用 Agent 会话存储
