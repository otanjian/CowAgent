## 当前代码的接入点

- 名册存储：`agent/workspace/session_prefs.py`，键为 `<agent_id or 'default'>::<session_id>`，`members` 是其一个字段。
- 名册的读取时机：
  - `AgentInitializer._load_tools` —— 构建运行时**时**判定 `agent_delegate` 是否装配（门槛：`conf().agent_delegation` 开启、已启用智能体 ≥2、`_is_shared_conversation` 为真）。
  - `AgentInitializer._teammates_getter` —— 每轮经 `_get_teammates()` 闭包动态解析，因此提示词里的团队段落是实时的。
- 会话设置写入：`channel/web/web_channel.py` 的 `SessionSettingsHandler.POST`，写入 `members` 后只调用 `AgentBridge.apply_session_prefs`（仅 model/permission）。
- 运行时缓存：`AgentBridge._agent_instances`，键 `(agent_id, session_id)`；`AgentBridge.clear_session(session_id, agent_id)` 驱逐单个键，`agent_id=None` 解析为默认智能体。持久化历史不受影响，重建时从 `conversation_store` 恢复。

## 方案与取舍

**选定：名册变化时退休该会话参与者的运行时。**

- 为什么不是"每轮重新装配工具"：工具集在 `create_agent` 时进入 Agent 实例，改成每轮可变会把 `agent_delegate` 的装配门槛判定搬到消息路径上，牵动提示词与工具 schema 的一致性，且与 `Agent.get_full_system_prompt` 只重建提示词、不重建工具的既有边界冲突。代价与风险都高于收益。
- 为什么驱逐**全部参与者**而不只是 owner：被 `@` 点名回答过的同事也会在本会话留下缓存运行时，其提示词同样携带名册；只重建 owner 会让它在成员变化后继续按旧名册说话。
- 为什么按**集合**比较名册：前端 `setTeamMembers` 传入的是有序列表，但顺序只对 owner 选取（团队对话首个选择）有意义；单纯重排不应让每个参与者都付出一次重建。存储仍保留原顺序。
- 为什么 `owner_agent_id` 允许为空：控制台的设置请求不携带 `agent`，此时会话按默认智能体命名空间存储；`clear_session(session_id, None)` 会解析为默认智能体，与写入命名空间一致。若跳过空值，运行时永远不会被退休（这是首版实现的缺陷，已由测试捕获）。

## 分阶段与门槛

单阶段，无 feature flag。门槛：新增回归测试在修复前必须失败（已核对：临时禁用驱逐逻辑时 3 条失败、`1 == 2`），修复后与既有 `test_direct_addressing.py`、`test_team_addressing.py`、`test_session_model_scope.py`、`test_agent_delegation.py`、`test_multi_agent_runtime.py` 等一并通过。

## 迁移/恢复

无数据迁移。`session_prefs.json` 结构与键不变。恢复路径即既有重建路径：下一次 `get_agent` 重建实例并从会话存储恢复历史。

## 未决实施参数

- 控制台设置请求不携带 `agent`，团队名册固定写入默认智能体命名空间；在非默认智能体的会话里邀请同事仍会错位。本 change 不处理，需另开 change 决定是让前端带上 `agent`，还是把团队改为按"当前会话智能体"寻址。
