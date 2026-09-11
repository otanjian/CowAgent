## Context

动机与范围见 `proposal.md`；行为契约见 `specs/user-personal-context/spec.md`、`specs/agent-chat-launch/spec.md` 与 `specs/agent-memory-explicit-add-tool/spec.md`。以下现状约束均已核对代码。

**个人层已预留但被短路：**

- `common/state_dir.py` 模块定位把状态分三类，`user_root` 明确承载「profile / preferences / memory / task records」，并说明"一个用户的偏好与哪个 Agent 在跟他说话无关"，因此位于 Agents 旁边。`user_root()` 在 `user_id` 非空时返回 `shared_root()/users/<user_id>`，为空时坍缩到 `state_root()`——这正是"迁移只改这个函数"的设计。
- 但 `_user_base(identity, base)` 在传了 `base` 时直接返回 `Path(base)`，而所有记忆调用都传 `base=<agent workspace>`（`agent/memory/config.py` 的 `memory_dir`/`memory_index_db`），于是用户层永远不可达。
- `agent/memory/storage.py` 的 `chunks` 已有 `user_id` + `scope` 与索引；`memory_index_db` 的 docstring 明确"索引仍每 Agent 一份，靠 `user_id`/`scope` 列隔离，不按路径"，并留下硬约束："miss one query and it is a cross-user leak, so there is exactly one place that may build these WHERE clauses"。
- 检索侧已实现过滤：`storage.py` 的 FTS5 / trigram / like 三条路径与 `vector_backend.py` 的 `(scope = 'shared' OR user_id = ?)`。
- 按用户的文件写入器已存在：`summarizer.get_main_memory_file(user_id)` / `get_today_memory_file(user_id)` / `write_daily_summary(user_id=...)` 等 → `memory/users/<id>/...`；`manager.sync()` 也能从路径推导 `scope="user", user_id=<id>`。
- 缺口：`user_id` 从未贯通。记忆工具构造（`bridge/agent_initializer.py`）不传 `user_id`；溢出 flush 读的 `getattr(self.agent, '_current_user_id', None)` 被读 5 处、赋值 0 处；每日 flush 与 `run_evolution_for_session` 同样不传。
- 已落地先例：用户私有项目走 `user_root()`（`agent/workspace/project_store.py`），隔离层已把 `user_root` 列为合法读写根（`agent/permission/isolation.py`）。

**会话与智能体锚点：**

- 会话存在 `<agent workspace>/memory/long-term/index.db` 的 `sessions`/`messages`，`sessions.owner` 已按 `current_identity().user_id` 写入；`list_sessions`/`load_history_page` 按 `owner` 过滤，但 `load_messages`（LLM 上下文恢复）**未**按 owner 过滤。
- 服务端缺省 `agent_id` 的解析：`_require_tenant_agent_binding` 先取租户 `default_agent_id`，否则要求仅一个绑定智能体，否则 403 `default agent ambiguous`。
- 前端：`multiAgentMode()` = `availableChatAgents().length > 1`；为真时 `onNewChatButton` 强制弹出选择菜单（单人 / 多智能体团队）。
- `AgentRouter.resolve` 在无显式 id 时返回**全局** `registry.default_agent_id`，非租户默认——Web 发送路径实际由 `_authorize_chat_session` 先做租户绑定校验，因此租户默认的解析必须在这一层收口。

**索引与提示词的同源问题：**

- `manager.sync()` 扫描 `self.config.get_memory_dir()`（智能体工作区），并用 `file_path.relative_to(workspace_dir)` 推导 `rel_parts`。记忆文件一旦位于 `user_root()`，既扫不到也会抛异常。
- 提示词自动加载的 `MEMORY.md` 取自智能体工作区根（`agent/prompt/workspace.py`），不是用户域。
- `memory_get` 只挡工作区越界，未校验用户边界，因此 `memory/users/<他人>/...` 在路径可猜时**可被读取**。

## Goals / Non-Goals

**Goals:**

- 让"进入对话"不再需要用户先选智能体，同时不删除智能体锚点。
- 让每个用户获得专属人设（可按会话叠加）与独立长期记忆，且**不新建智能体**。
- 个人记忆在同一用户的**所有有权使用的智能体**下一致可见。
- 隔离可验收：他人/他租户读不到个人记忆；legacy 模式行为不变。

**Non-Goals:**

- 不做"每个用户一个智能体"，不改 `agent_bindings` 的一对一绑定。
- 不做每用户一个独立记忆数据库（与 `memory_index_db` 的既有决定冲突）。
- 不搬迁、不改写既有会话 ID、消息内容与智能体工作区。
- 不做跨租户或跨设备的个人记忆同步（不在本轮）。
- 不改智能体↵租户的资源授权模型（`role-resource-authorization` 不变）。

## Decisions

### D1 默认智能体是综合会话锚点，不新建 per-user 智能体

个人层放在 `user_root` 旁边，会话继续挂在默认智能体上。用户不为智能体做决定；选择器降级为主动切换专家。

- 备选：默认给每个用户建一个专属智能体。否——用户可属多租户，"一人一智能体"需退化为"一人一租户一智能体"；资源授权目录膨胀 N 倍使"角色→智能体授权"失去意义；每个智能体一套工作区目录 + 人设文件 + 索引 DB，成本与生命周期负担不可控；且把"组织数字员工"的定位漂移成"个人助理"。
- 备选：允许 `agent_id=''` 的无锚点会话。否——执行仍需要一个工作区/技能/工具/模型配置包，最终还是要解析到某个智能体，只是把问题藏起来，却要改动 `sessions`/`runs`/取消键与审计。

### D2 租户恒有可解析默认智能体，缺席 `agent_id` 不再歧义拒绝

解析顺序（单一实现，供 `_require_tenant_agent_binding`、`_require_session_owner`、`_tenant_default_agent_id` 共用，消除三处口径分叉）：租户已配置 `default_agent_id` → 该租户唯一绑定智能体 → 多个绑定时取**稳定 id 最小者**。只有租户一个智能体都没有时才拒绝。

**该解析是只读的，不在读取路径上写租户配置。** 初稿曾计划"补齐默认并落审计"，实现时改为纯确定性解析：GET/会话读取不应产生写副作用，且写库会让一个读取路径受租户版本冲突（`tenants.version`）影响；确定性顺序已能保证同一绑定集合下结果恒定。显式指定默认仍由既有管理动作（`set_tenant_default_agent` / `appoint_tenant_default_agent`）承担，不在本 change 内新增。

- 备选（已否决）：读取时按稳定规则写入默认并审计。写副作用 + 版本冲突放大，收益仅是"把结论固化"，而确定性解析已等价。
- 备选：前端总是显式发送某个智能体来绕过歧义。否——那是把不确定性推给客户端，缺省请求仍可被 403，且前端"随便挑一个"是静默的任意选择。
- 备选：缺省时回落到**全局**默认智能体。否——全局默认可能未绑定当前租户，属跨租户泄漏（`tenant-resource-isolation` 已明确禁止回退全局默认）。

### D3 个人人设走 `extra_system_suffix` 叠加，不改人设文件

在 `bridge/agent_bridge.py` 的 `_apply_scene_context` / `_apply_employee_context` 之后新增 `_apply_user_persona_context(agent)`，读用户域档案并追加到 `extra_system_suffix`。三段（场景 / 员工 / 个人）共存、顺序稳定；`Agent.get_full_system_prompt` 每轮重读并追加 suffix，天然即时生效。

- 备选：为每个用户覆写 `AGENT.md`。否——`AGENT.md` 是智能体级事实，且 `executor.py` 明确"persona is workspace-global (not per-user)"；覆写会造成智能体之间与用户之间的双重漂移。
- 备选：启用 `builder.py` 中从未被调用的 `user_identity` 结构化段。可作为实现细节，但本轮以 suffix 叠加为主，避免同时引入两条注入链。

### D4 个人记忆文件落用户域，索引每智能体一份并覆盖用户域

- 记忆**文件**：`scope=user` 落 `user_root()/memory/users/<user_id>/`（含 `MEMORY.md` 与 daily）。
- 记忆**索引**：保持每智能体一份 `index.db`；各智能体的 `sync()` SHALL 额外扫描该用户的用户域记忆目录，使同一份用户文件在各索引中都获得一致 `user_id`/`scope` 归属 → 换智能体仍可见。
- 共享记忆：`scope='shared'` 仍来自智能体自身工作区（`MEMORY.md`、共享 memory 文件）。

- 备选：每用户一个独立 `index.db`，检索时合并。否——与 `memory_index_db` 的既有决定冲突（per-user DB 会让"我知道 Wang 什么"变成 N 文件 fan-out）；且新增第二条检索路径会破坏"只有一个地方构造 WHERE"的硬约束。
- 需要处理的实现细节：`sync()` 的扫描根与 `relative_to(workspace_dir)` 推导必须同时支持"工作区内"与"用户域"两种来源，且用户域文件必须显式带上 `user_id`（不能靠相对路径推导，因为它在 workspace 之外）。

### D5 `scope=user` 的 `user_id` 只来自已验证运行时身份

记忆工具不再接受调用方提供的用户标识来决定归属；`user_id` 由 `current_identity()` 解析（Web 路径已由 `_web_runtime_identity_snapshot` 校验过数据库会话身份）。工具构造、溢出 flush、每日 flush、evolution 一律注入同一来源。移除幽灵 `_current_user_id` 读取点。

- 备选：保留工具参数 `user_id` 并默认缺省。否——`memory_add` 若可携带任意 `user_id`，等于把跨用户写入能力交给模型/调用方。

### D6 自动固化默认私有

溢出 flush、每日摘要与 dream 的产物默认 `scope=user` + 当前用户；共享知识继续由显式 `scope=shared` 或智能体维护的 `MEMORY.md` 承担。避免"用户对话内容被自动蒸馏进全租户可见的共享记忆"。

### D7 隔离加固：单点 WHERE、`memory_get` 边界、`load_messages` owner

- 检索 WHERE 只在既有唯一点构造，新增路径 MUST 复用，不得旁路。
- `memory_get` SHALL 在解析路径后校验用户边界：`memory/users/<other>/...` 对非本人拒绝；`memory/users/<self>/...`、`memory/shared/...`、根 `MEMORY.md` 按既有语义。
- `load_messages` 补 `owner` 过滤，使 LLM 上下文恢复与列表读取同一口径。

### D8 legacy 坍缩，不引入开关与迁移

`user_id` 为空时 `user_root()` 返回 `state_root()`、`_user_base` 走用户域分支的路径与现状一致，检索在全可见语义下退化，因此行为与变更前完全相同。存量数据不动，无 schema 变更，无迁移脚本。

### D9 默认智能体是租户共享的，私有归属只能显式设置

实现时在真实库上发现：`agent_bindings.private_owner_user_id` 被两处写入**从操作者身份隐式推定**——`register_default_tenancy` 打上初始管理员，`_adopt_created_agent_for_tenant` 打上创建者。而该列的语义是**独占读取门禁**（`_require_private_owner`，在聊天授权入口也生效）：非 owner 且非租户管理员 → 403。

真实数据佐证：`default` 租户的 `default`/`erpnext` 两个智能体 owner 均为 `admin`，且该租户 `default_agent_id` 为空。于是"必须选智能体"实际是**两道拦截叠加**——普通成员既被"无默认 → 403 ambiguous"挡，也会被"默认智能体是 admin 私有 → 403"挡。只修前者，成员看到的结果不变。

因此确立不变量：**默认智能体恒为租户共享**（`private_owner_user_id` 为 NULL）。
- 创建路径不再推定 owner（`_adopt_created_agent_for_tenant` 绑为租户共享）；
- **任命为默认即显式共享**：`_appoint_tenant_default_agent` 清空私有归属并审计，因为"设为租户默认"这个动作本身就是"这是全租户的共享入口"的显式意图；
- 新增显式操作 `make_agent_tenant_shared`（`bind_agent` 只能补写、不能清空 owner，无法承担）；
- 新增幂等校正 `ensure_shared_default_agents()`，只处理每个租户**实际可解析为默认**的那一个智能体，非默认的私有智能体保持不动；
- 缺省解析优先选租户共享的智能体，避免有共享可选时把入口锚定在仅私有智能体上。

- 备选：保留私有默认，让成员另选共享智能体。否——那样"不强制选择"只对 owner/管理员成立，与 D1/D2 的定位自相矛盾。
- 备选：任命默认时**拒绝**私有智能体而改为要求先手动共享。否——多一步且没有额外安全性；任命动作本身已由平台/租户管理员门禁，且落审计。
- 备选：校正时清空全部智能体的 owner。否——会摧毁合法的私有智能体；校正只针对可解析为默认者。

## Risks / Trade-offs

- **跨用户泄漏是本变更的首要风险**。缓解：单点 WHERE 约束、`memory_get` 边界、双用户隔离用例（A 的个人记忆 B 检索不到、共享记忆双方可见）、legacy 全可见回归。
- **`sync()` 覆盖用户域会扩大索引范围**：需确认用户域文件变更能触发重索引且不与其他用户文件混淆；以 `user_id` 显式标注而非路径推导，避免"users 出现在路径里"的解析歧义。
- **多智能体下同一用户记忆的重复索引**：同一用户文件被 N 个智能体索引，属可接受的冗余（查询仍单库、按 `user_id` 过滤）；不一致风险由"文件是唯一事实来源"消解。
- **补默认智能体是写操作**：对存量租户补齐默认需落审计并可重复执行（幂等），不得悄悄改变已配置默认的租户。
- **个人人设注入的会话归属**：`get_agent()` 结果按 `(agent_id, session_id)` 缓存，个人人设 MUST 在会话属于该用户时注入；共享/团队会话不适用个人人设（避免"最后访问者覆盖"）。

## Migration Plan

无 schema 变更、无数据迁移。用户域目录按需创建（首次写入时 `ensure`）。存量会话与记忆保持原归属；`chunks.user_id`/`scope` 在既有行上为空/`shared`，语义等价于当前行为。存量"多智能体且无默认"的租户在首次读到歧义时按确定性规则补齐默认并审计。

## Open Questions

- 个人档案的呈现与编辑入口（控制台页面 vs 对话内编辑）留给后续 change；本轮先保证运行时注入与隔离正确。
- 个人记忆是否需要跨设备/跨租户同步，待 PRD 原文恢复后核对。
- 共享/团队会话中是否允许注入调用者个人人设（当前：不注入），需产品确认。
