## Why

**一、没有"个人"这一层。** 智能体是租户范围的组织资源（岗位/数字员工），没有任何载体承接"某个用户自己的形象与长期记忆"。`agent_bindings.private_owner_user_id` 只是单个可空的私有归属标记，用于读范围，不是个人档案，也不构成个人记忆域。用户要的是「专属人设 + 独立长期记忆」。

**二、架构早就预留了正确的位置，但被短路。** `common/state_dir.py` 明确把状态分三类，并把「profile / preferences / memory / task records」划给 **Per end user（`user_root`）**："Wang likes email over phone calls" 是关于 Wang 的事实，与哪个 Agent 在跟他说话无关，所以它位于 Agents **旁边**而不是其中之一。`user_root()` 在有 `user_id` 时已解析到 `shared_root()/users/<user_id>/`；记忆 schema 已带 `chunks.user_id` 与 `chunks.scope`（`shared`/`user`/`session`）及索引；检索已实现 `(scope = 'shared' OR user_id = ?)`；按用户的文件写入器（`get_main_memory_file(user_id)` → `memory/users/<id>/MEMORY.md`）也已存在。**但所有生产调用都传 `base=<智能体工作区>`**（`agent/memory/config.py` 的 `memory_dir`/`memory_index_db`），而 `_user_base` 一旦拿到 `base` 就直接返回它、忽略身份；且 `user_id` 从未贯通（`_current_user_id` 被读 5 处、赋值 0 处）。于是整套机制坍缩回智能体作用域。

**三、进入对话必须先选一个智能体。** 会话物理上存在某个智能体的工作区 DB 里，服务端在未指定 `agent_id` 时只接受「租户默认」或「唯一绑定智能体」，否则 403 `default agent ambiguous`（`_require_tenant_agent_binding`）；前端在可聊智能体 > 1 时，"新对话"强制弹出选择菜单（`multiAgentMode()` → `onNewChatButton`）。个人层本应让"选谁"不再决定"你在跟谁说话"，但用户仍被拦在选人菜单前，等于个人层没有入口。

**四、两件事强耦合，分开做会失效。** 记忆索引是**每智能体一个 `index.db`**，`chunks.user_id` 只在**同一智能体索引内**分区。若个人记忆只落在某个智能体的索引里，用户切到另一个智能体就**看不到自己的个人记忆**——"个人记忆跨智能体一致"落空。必须同批解决。

## What Changes

- **综合会话入口（默认智能体锚点）**：每个租户 MUST 恒有一个可解析的默认智能体；未显式指定 `agent_id` 的会话请求 SHALL 解析到该默认智能体，而 MUST NOT 因"多智能体且未配置默认"返回歧义拒绝。前端进入「对话」MUST NOT 强制选择智能体，选择器保留为"主动切换专家"的可选动作。语义：默认智能体 = 个人/综合对话锚点，其他智能体 = 专家。
- **个人人设层**：新增按 `user_root()` 存放的个人档案；会话构建（`get_agent()`）时在场景/员工上下文之后叠加注入，写入 `extra_system_suffix`，三段共存且顺序稳定。MUST NOT 新建智能体、roster 条目或工作区。
- **个人长期记忆域**：`scope=user` 的记忆 SHALL 绑定到当前请求主体（`current_identity().user_id`）；记忆**文件** SHALL 落在用户域（`user_root()` 下），MUST NOT 落进智能体工作区；检索 SHALL 恒为 `(scope='shared' OR user_id = ?)`。
- **跨智能体一致**：个人记忆 SHALL 对同一用户在所有其有权使用的智能体下可见。实现上各智能体的索引 SHALL 覆盖用户域记忆文件，使同一份用户事实在各索引中获得一致的 `user_id`/`scope` 归属；MUST NOT 出现"换一个智能体就丢个人记忆"。
- **写入边界**：`scope=user` 的 `user_id` 只 SHALL 来自已验证的运行时身份，MUST NOT 接受调用方传入的任意用户标识。
- **自动固化默认私有**：溢出 flush、每日摘要与 dream 等自动固化产物 SHALL 默认归个人私有域（`scope=user`, 本人 `user_id`），MUST NOT 默认写入共享域。共享知识仍由显式 `scope=shared` 或智能体自身维护的 `MEMORY.md` 承担。
- **隔离加固**：`memory_get` SHALL 校验用户边界（当前可读 `memory/users/<他人>/...`）；会话上下文恢复（`load_messages`）SHALL 按 `owner` 过滤；检索 WHERE MUST 只在单一入口构造。
- **legacy 兼容**：`user_id` 为空时，`user_root()` 坍缩到 `state_root()`，行为与本次变更前**完全一致**，不引入迁移脚本，不新增开关。

## Impact

- **代码**
  - `common/state_dir.py`：`memory_dir`/`memory_file` 的 `base` 语义按用户域解析；`memory_index_db` 保持每智能体。
  - `agent/memory/config.py`、`agent/memory/manager.py`、`agent/memory/summarizer.py`：记忆文件根切到用户域、`sync()` 覆盖用户域文件并修正 `relative_to(workspace_dir)` 推导。
  - `bridge/agent_initializer.py`、`agent/protocol/agent_stream.py`、`agent/protocol/agent.py`、`agent/evolution/trigger.py`、`agent/evolution/executor.py`：把 `current_identity().user_id` 贯通到记忆工具构造、溢出 flush、每日 flush 与 evolution；移除幽灵 `_current_user_id`。
  - `agent/tools/memory/memory_get.py`：用户边界校验。
  - `agent/memory/conversation_store.py`：`load_messages` 的 `owner` 过滤。
  - `bridge/agent_bridge.py`：新增个人人设注入，接在 `_apply_employee_context` 之后。
  - `agent/prompt/workspace.py`：提示词加载的 `MEMORY.md` 支持用户域。
  - `channel/web/web_channel.py`：默认智能体的确定性解析与"多智能体无默认"的补齐；不再以歧义 403 阻断缺席的 `agent_id`。
  - `channel/web/static/js/console.js`：进入对话不强制选择，默认智能体直连。
- **API**：不新增端点、不改响应结构。缺省 `agent_id` 的会话请求从"可能 403"变为"解析到租户默认"。
- **数据**：新增用户域目录 `shared_root()/users/<user_id>/`（profile + memory）；`chunks.user_id`/`scope` 从"预留"转为实际写入。已有智能体工作区、会话 ID、消息内容不变，MUST NOT 搬迁历史。
- **安全**：个人记忆的读写边界以已验证运行时身份为准；`memory_get` 堵住按路径猜测读取他人个人记忆的越界；跨用户/跨租户读取仍拒绝。
- **兼容**：legacy 单实例（无 `user_id`）行为不变。
- **测试**：新增用户域记忆与个人层套件；回归 `agent-memory-explicit-add-tool`、`tenant-resource-isolation`、`session-history-workbench`、`agent-chat-launch` 相关既有用例。

### 与 PRD 的关系

- 需求基线按 `openspec/config.yaml` 指向的 PRD-01～12 v1.2。仓库所称 PRD 原文沿袭既有 change 的记录**当前仍缺失**（`doc/优化规划/` 目录不存在），故本次行为基线取自 `openspec/specs/` 下既有规范（`tenant-resource-isolation` 的「个人与共享资源有明确边界」、`agent-memory-explicit-add-tool` 的「记忆写入作用于当前用户」、`agent-chat-launch`、`session-history-workbench`）与已交付代码。PRD 原文恢复后 MUST 复核三处口径：① 是否要求"个人助理"必须是独立可配置实体；② 自动固化的默认归属（个人私有 / 共享）是否另有规定；③ 个人记忆是否要求跨租户或跨设备同步。
- **数据唯一归属**：个人档案与个人记忆文件归属 `user_root()`，由记忆子系统写入；智能体实体、工作区与会话仍归属 Agent Registry / 各智能体 workspace，由既有路径写入。本 change MUST NOT 在身份库写入记忆内容，也 MUST NOT 在智能体工作区复制个人档案。跨智能体共享的是**用户域文件**，不是索引行。
- **跨 change 依赖**：`agent-digital-employee-capabilities` 已交付的 `extra_system_suffix` 注入链是本 change 个人人设注入的前置；`open-database-runtime` 的租户绑定与隔离是本 change 默认智能体解析的前置；`copy-default-tenant-agents` 的租户共享根归属决定个人档案的物理位置。本 change MUST NOT 修改上述规范的既有需求，只在其上增加个人维度。

## Capabilities

### New Capabilities

- `user-personal-context`: 以用户为中心的个人层——按 `user_root()` 存放的个人人设档案与个人长期记忆域；`scope=user` 绑定已验证运行时身份；个人记忆跨智能体一致可见；自动固化默认私有；读写边界与 legacy 兼容。

### Modified Capabilities

- `agent-chat-launch`: 增加"未选择智能体时的综合会话入口"——租户恒有可解析默认智能体，缺席 `agent_id` 的会话请求解析到它而非歧义拒绝；进入对话不强制选择；显式目标失效仍不得静默替换。
- `agent-memory-explicit-add-tool`: 「记忆写入作用于当前用户」由"工具持有 `user_id`"改为"从已验证运行时身份解析 `user_id`，并在所有有权使用的智能体下一致归属"。
