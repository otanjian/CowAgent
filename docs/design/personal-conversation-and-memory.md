# 个人层：综合会话锚点、专属人设与独立长期记忆

对应 change：`openspec/changes/personal-conversation-and-memory/`。
本文说明**实现落地后的材料与取舍**，供后续维护与验收引用；行为契约以该 change 的
`specs/` 为准。

## 1. 一句话结论

用户要的是「专属人设 + 独立长期记忆」，实现方式是**补齐既有的用户层
（`user_root()`）**，并让「进入对话」不再先要求选一个智能体；**不新建
per-user 智能体**。

## 2. 为什么不新建「一人一个智能体」

这是本 change 最重要的取舍，记录在此以免后续被重新提出。

| 维度 | 一人一智能体 | 补齐 `user_root` 用户层（本方案） |
| --- | --- | --- |
| 用户与租户 | 用户可同属多租户，方案退化为"一人一租户一智能体" | 用户层天然在租户之内，多租户归属清晰 |
| 资源授权 | 智能体数量膨胀 N 倍，"角色 → 智能体授权"失去意义 | 智能体仍是租户内的组织资源，授权目录不膨胀 |
| 运行成本 | 每个智能体一套工作区 + 人设文件 + `index.db`，生命周期不可控 | 复用既有每智能体一份的 `index.db`，无新增库 |
| 产品定位 | 把"组织数字员工"漂移成"个人助理" | 保持"数字员工"定位，个人层是**叠加**而非替代 |
| 绑定语义 | `agent_bindings` 是租户绑定，硬塞用户维度会污染 | `scope=user` + `user_id` 本就是记忆 schema 的既有维度 |

架构早就预留了正确位置：`common/state_dir.py` 把 profile / preferences /
memory / task records 划给 **Per end user（`user_root`）**——"Wang 喜欢电话而非
邮件"是关于 Wang 的事实，与哪个 Agent 在跟他说话无关，所以它位于 Agents
**旁边**而不是其中之一。本 change 做的是让生产调用**真的走到这一层**。

## 3. 个人层的三块材料

### 3.1 个人人设（`user_root()/PERSONA.md`）

- 路径由 `common/state_dir.py::persona_file()` **唯一定义**，调用方不得自行拼接。
- 注入点在 `bridge/agent_bridge.py::_apply_user_persona_context`，接在
  `_apply_scene_context` / `_apply_employee_context` **之后**，追加到
  `extra_system_suffix`。顺序恒为：**场景 → 员工 → 个人**。
- 每轮 `get_agent()` 重新读取，所以档案改动**下一个请求即生效**，无需重启。
- 没有档案 → 不注入空段；也没因此拒绝会话。
- **会话归属守卫**：`get_agent()` 结果按 `(agent_id, session_id)` 缓存，
  共享/团队会话若注入调用者个人段，会变成"最后访问者决定"。因此仅当会话
  属于当前用户时注入；他人会话不注入。

### 3.2 个人长期记忆（`user_root()/MEMORY.md`、`user_root()/memory/**`）

- 记忆**文件**落在用户域，`scope=user` 与 `user_id` 来自
  **已验证运行时身份**（`current_user_id()`），不接受调用方传入。
- **跨智能体一致**：记忆索引是每智能体一份 `index.db`，`user_id` 只在
  **同一索引内**分区。因此 `MemoryManager.sync()` 会额外扫描当前用户的用户域
  文件（工作区之外），显式标注 `user_id`/`scope=user` 并使用显式索引标签，
  而不是用 `relative_to(workspace_dir)` 推导。这样同一份用户事实在每个智能体
  的索引里都获得一致归属，"换个智能体就读不到自己的记忆"不会出现。
- 检索 WHERE 只在 `agent/memory/storage.py::_scope_filter` **单一入口**构造，
  FTS5 / trigram / LIKE / 向量 metadata 全部复用它——漏一个 `AND` 就是跨用户
  泄漏，靠人眼审查三份近乎相同的 SQL 不可靠。
- 自动固化（溢出 flush、每日摘要、dream）默认写**个人私有域**；共享知识仍由
  显式 `scope=shared` 或智能体自身维护的 `MEMORY.md` 承担。
- 读取边界：`memory_get` 拒绝对 `memory/users/<他人>/...` 的读取；会话上下文
  恢复（`load_messages`）按 `owner` 过滤，与 `list_sessions` 同一口径。
- 跨租户：用户域位于各租户的 `shared_root()` 之下，租户边界仍由
  `agent/permission/isolation.py` 拦截，本 change **未放宽**该边界。

### 3.3 默认智能体 = 综合会话锚点

- 每个租户恒可解析出一个默认智能体：**配置默认 → 租户共享中稳定 id 最小 →
  其余**（`IdentityService.resolved_default_agent_id`，只读、确定性）。
- 未显式指定 `agent_id` 的会话请求解析到该默认，不再因"多智能体且未配置默认"
  返回 403 `default agent ambiguous`。跨租户的全局默认**绝不**回退借用。
- 前端：进入对话与"新对话"**不再强制选择**；`multiAgentMode()` 只决定
  "切换/团队"这个**可选**入口（新对话按钮右侧的 caret）是否出现。
- **默认智能体恒为租户共享**（`private_owner_user_id = NULL`）。这是实现期发现的
  叠加拦截：历史写入会把创建者/初始管理员推定为私有 owner，使默认智能体只有
  该用户可用，其他成员仍被拦。因此：新建/采用不再推定 owner；任命默认会清空
  owner；提供显式"转为租户共享"操作；并提供幂等校正
  `ensure_shared_default_agents()` 与运维入口
  `cow management share-default-agents [--dry-run]`。私有归属是**显式**动作，
  永不推定。

## 4. 兼容与迁移

- **无 schema 变更**：`chunks.user_id`/`scope` 早已存在，本 change 只是开始实际写入。
- **无数据迁移**：既有智能体工作区、会话 ID、消息内容、历史记忆不搬迁、不改写。
- **legacy 行为不变**：`user_id` 为空时 `user_root()` 坍缩到 `state_root()`，
  路径、可见范围、既有会话读取与变更前完全一致，不引入开关或新的拒绝路径。
- 用户域目录按需创建（`ensure`），只读请求不产生副作用目录。

## 5. Non-Goal（留待后续 change）

- **个人档案与个人记忆的编辑入口**（控制台页面 vs 对话内编辑）不在本轮范围；
  本轮先保证运行时注入与隔离正确。档案目前以文件形式存在，可由运维/工具写入。
- 共享/团队会话中是否允许注入调用者个人人设（当前：**不注入**），需产品确认。
- 规范中"个人助理是否为独立实体 / 自动固化默认归属 / 个人记忆是否跨租户或跨设备
  同步"三处口径，当前以 `openspec/specs/` 相关 capability（`self-account-context`、
  `tenant-resource-isolation`、`agent-memory-explicit-add-tool`）为准；需要变更时另开 change。
