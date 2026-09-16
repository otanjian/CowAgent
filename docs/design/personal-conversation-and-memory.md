# 个人层：综合会话锚点、专属人设与独立长期记忆

## 2026-09-15 方案变更（已实施，2026-09-16 复核）

用户记忆仍按当前 tenant/user 归属，但从现有记忆管理共用页面维护，不建立独立个人功能。详情“设为默认”对所有用户统一设置本人当前租户偏好，租户默认单独管理且不再隐式清除私有 owner。第 3.3 节已更新为新方案；其余关于早期“不新建个人智能体”等取舍只代表当时阶段，不能用于阻止现有供应助理或用户私有对象。

最新方案：[统一控制台与数据范围方案](unified-console-access-plan.md)；实施契约：[unify-console-by-data-scope](../../openspec/changes/unify-console-by-data-scope/proposal.md)。

**实际状态**：第 3.3 节的用户默认/租户默认已实现并取得切片验收（4.4–4.8、`evidence/4-4-*`、`4-6-*`、`4-7-*`、`4-8-*`）；
个人记忆改由统一记忆页面维护的**读面**已验收，**写面在数据库形态下限管理资格**——「成员写本人私有智能体记忆」
因智能体记忆根就是租户共享根而**未覆盖**（`evidence/5-1b-write-path-design.md` §6）。
逐项判定见 [`evidence/8-5-doc-closure.md`](../../openspec/changes/unify-console-by-data-scope/evidence/8-5-doc-closure.md) §2。

---


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

### 3.3 用户默认与租户默认（2026-09-15 新方案，已实施并验收）

- 正式智能体详情的“设为默认”统一设置当前用户在当前租户的偏好，复用 `memberships.default_agent_id` 并增加独立版本。普通用户与管理员操作一致，成功后不需要重启。
- 新会话解析顺序：有效用户默认 → 本人可用的租户共享默认 → 获准共享候选的稳定标识 → 本人可用私有候选的稳定标识。无合法候选则拒绝，不能从全租户私有对象中任取，也不能借用全局或其他租户默认。
- 解析只读；已有会话、显式选择、后台任务及渠道绑定不因用户默认变化重绑。系统供应只初始化空偏好，不覆盖用户选择。
- 租户默认在独立管理配置中明确命名，只接受已共享对象。用户默认可以是本人私有对象；任何默认设置、启动校正或数据迁移都不能清除私有 owner。
- 普通用户创建租户首个智能体仍归本人私有，不因“首个”自动共享。本人维护走共用生命周期，模型/工具/技能依赖继续验权。
- 旧“任命默认即清空 owner”及 `share-default-agents` 的相关记录仅保留在历史验收材料；新迁移清理非法租户默认指针并保留私有归属。

> **落点（2026-09-16）**：`set_user_default_agent`（`auth/service.py:861`）与详情动作 `set_user_default`
> （`channel/web/web_channel.py:10820`、`:10846`）；租户默认保留 `set_tenant_default_agent`（`auth/service.py:1603`），
> 旧 `appoint_tenant_default_agent`（`:1620`）仍受管理资格保护且拒绝私有目标；非法指针清理与 owner 保留由
> `_migration_25`（`auth/store.py:1233-1345`）完成，并由本 change 的迁移演练验证「无迁移改写
> `private_owner_user_id`」。**已知差异**：租户默认无乐观锁（规范未要求），`agent_bindings.agent_id`
> 主键使「一个 Agent 绑两个租户」不可表示，仓库无「克隆来源」模板判据——见
> `evidence/4-7-lifecycle-and-defaults-verification.md`。

## 4. 兼容与迁移

- **无 schema 变更**：`chunks.user_id`/`scope` 早已存在，本 change 只是开始实际写入。
- **无数据迁移**：既有智能体工作区、会话 ID、消息内容、历史记忆不搬迁、不改写。
- **legacy 行为不变**：`user_id` 为空时 `user_root()` 坍缩到 `state_root()`，
  路径、可见范围、既有会话读取与变更前完全一致，不引入开关或新的拒绝路径。
- 用户域目录按需创建（`ensure`），只读请求不产生副作用目录。

## 5. Non-Goal（留待后续 change）

- **个人档案与个人记忆的编辑入口**（控制台页面 vs 对话内编辑）不在本轮范围；
  本轮先保证运行时注入与隔离正确。档案目前以文件形式存在，可由运维/工具写入。

  > **现状（2026-09-16）**：记忆的编辑入口已由 `unify-console-by-data-scope` 落地一部分——
  > 统一记忆页面提供列表/正文/写入，本人用户记忆可写（`POST /api/memory/personal`），
  > 管理员可写获准共享记忆；**成员写本人私有智能体记忆未交付**（写面限管理资格，
  > 前置是每个智能体独立记忆根，见 `evidence/5-1b-write-path-design.md` §6）。
  > 个人人设的编辑入口仍未实现。
- 共享/团队会话中是否允许注入调用者个人人设（当前：**不注入**），需产品确认。
- 规范中"个人助理是否为独立实体 / 自动固化默认归属 / 个人记忆是否跨租户或跨设备
  同步"三处口径，当前以 `openspec/specs/` 相关 capability（`self-account-context`、
  `tenant-resource-isolation`、`agent-memory-explicit-add-tool`）为准；需要变更时另开 change。

## 6. 取代关系与判定依据

本文作为旧「个人层」设计材料，被
[统一控制台与数据范围方案](unified-console-access-plan.md) 取代的部分是**入口与页面形态**：
个人页面不再是独立管理入口，用户记忆与用户默认从现有正式页面维护。保留的是数据事实——
`user_root()` 归属、`scope=user` + `user_id` 的可信来源、唯一的记忆版本与检索协议、
跨获准智能体的一致性。本 change 对旧要求的逐行取代矩阵与实际落点见
[`evidence/8-5-doc-closure.md`](../../openspec/changes/unify-console-by-data-scope/evidence/8-5-doc-closure.md) §3。
