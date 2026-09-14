## Why

普通成员在被自动创建了私属「智能办公助理」之后，控制台「智能体」页显示**「暂无可用的智能体」**，也进不去对话——而该助理其实已经创建成功。

实测（租户 `test15` /「AI启航团队」，用户 `RC001` / Rock，内置 `member` 角色，创建于 2026-09-14 11:42）：

- `agent_bindings` 已有 `my-assistant-admin-test15-RC001`，`private_owner_user_id` 为该用户；
- `memberships.default_agent_id` 已登记为该助理；
- 审计为 `member.personal_agent.create` / success，`source_agent_id=my-assistant-admin-test15`；
- 工作区与 `USER.md` 已个人化（姓名 Rock、用户名 RC001）。
- 但同一用户请求 `/api/agents?view=workbench` 返回**成功且为空**，界面因此只显示空态。

根因是两处口径互相抵消：

1. **所有权只被实现为「拒绝他人」，从未被实现为「许可本人」。** `agent_bindings.private_owner_user_id` 只用于独占读取门禁；`resource_ids_for` / `check_resource_action` 是纯角色资源授权匹配，`bind_agent` 与 `set_member_default_agent` 也不写任何资源 grant。内置 `member` 角色只有功能权限（含 `agent.read` / `agent.use`）与菜单 grant，**没有任何 `agent:<id>` 资源 grant**，于是该成员的 `agent:*` 可读集合为空。
2. **唯一的可达性放宽恰好把自己的私属排除在外。** `agent-chat-launch` 的放宽只认「**租户共享**的**解析默认**」；而对该成员解析默认正是其本人私属的助理（`resolved_default_agent_id` 第 1 层返回它）。两个条件互斥：私属那个因带 `private_owner_user_id` 被放宽拒绝，租户共享默认那个因 `agent_id != tenant_default` 被放宽拒绝，其余候选因无 grant 被拒绝——**可见集为空**。

因此这**不是新增需求，而是既有规范未被实现**：`user-personal-agent-provisioning` 已写明「其可读与可用 SHALL 只对所有者本人、以及本租户负有管理职责的管理员开放」，并有场景「**所有者本人可读可用** … U 可正常与其对话」。`add-tenant-default-agent-selection` 也已把「其为该主体本人的私属资源」写进个人默认层的可到达条件；本 change 补齐使该可到达性真正成立的那一环。

产品意图佐证（`doc/产品规划.md` 3.1，非规范基线，规范仍以 `openspec/specs/` 为准）：3.1.1 把用户级资源的范围写为「**个人资源** + **被授权的**租户资源」，并把「创建与维护个人智能体、工具与技能」列为用户级动作而未标注为后续版本——即**个人智能体属当前版本能力**，与 3.2 正文及 5.3 的「后续版本」表述相矛盾。**该版本归属已由产品确认取 3.1.1 口径：个人智能体为当前能力**，故本 change 是对既有能力的实现补齐，不涉及改动任何已归档能力。文档侧的口径统一按任务 5.5 处理。

管理端之所以未察觉：租户管理员在投影中被整体豁免（不过滤逐资源 grant），**管理员登录能看到全部智能体**，包括成员那份私属助理。

## What Changes

- **确立「所有权即可达」为授权来源之一**：私属资源的所有者本人，对其**自己的**私属 Agent 天然持有 `agent.read` 与 `agent.use`，**无需**逐资源 grant。该规则 SHALL 在授权解析的统一入口生效（`.resource_ids_for` / `check_resource_action` 的同一判定），使工作台投影、卡片就绪判定与发送路径**共用同一结论**，不出现「卡片可见但发送被拒」。
- **可达性放宽按「可达的解析默认」表述**：把 `agent-chat-launch` 现要求从「**租户共享**的解析默认」推广为「**租户共享的解析默认，或调用者本人私属的解析默认**」。放宽边界同步收紧：MUST NOT 扩展到非默认智能体、MUST NOT 扩展到**他人**私属、MUST NOT 跨租户。
- **所有权不放宽写动作**：`agent.edit` / `agent.enable` 仍须逐资源 grant，保持「可达 MUST NOT 蕴含可改」。
- **无可见项时区分两种原因**：工作台列表在**成功但为空**时回带稳定原因码，区分「租户确无智能体」与「存在候选但均不可达（授权不足）」；服务端在该情形记录可诊断告警。当前两者都只表现为空数组，把授权问题误报成「系统里没有智能体」，这正是本缺陷未被发现的原因之一，且管理端因豁免而看不到。
- 不新增表或列，不引入用户级 grant 载体：`private_owner_user_id` 仍是所有权的唯一来源。
- i18n 三语系补齐新增的状态文案，并同步 `tests/fixtures/console_i18n_snapshot.json`。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `rbac-authorization`: 「固定个人和租户共享资源策略」补充**所有权作为授权来源**——私属 Agent 的所有者本人对其自己的资源持有读取与使用资格，MUST NOT 以缺少逐资源 grant 为由隐藏或拒绝；他人私属与跨租户边界不变，写动作不放宽。
- `user-personal-agent-provisioning`: 「私属归属与租户边界」把「所有者本人可读可用」明确为**不以逐资源 grant 为条件**，并覆盖工作台可见性与发起对话两条路径。
- `agent-chat-launch`: 「共享默认智能体的可达性不依赖逐资源授权」的适用范围由「租户共享的解析默认」推广为「可达的解析默认」（租户共享 **或** 本人私属），并保持不得扩展到非默认、他人私属与他人租户。
- `agent-workbench`: 新增「无可见项时区分无对象与不可达」——成功空态 SHALL 给出稳定原因，MUST NOT 让「存在但不可达」与「确无智能体」共用同一种呈现。

## Impact

- 后端授权解析：`auth/service.py`（`resource_ids_for` / `check_resource_action` 接入所有权判定；避免每请求重复加载绑定）。
- 后端投影与就绪：`channel/web/web_channel.py`（`_iter_tenant_agents` 与 `_workbench_chat_readiness` 的放宽判定统一到同一入口；空态原因码）。
- 前端：`channel/web/static/js/console.js`（空态原因呈现）；`channel/web/static/js/i18n/agents.js`（zh / zh-Hant / en）。
- 测试：`tests/test_agent_workbench.py`、`tests/test_identity_agent_bindings.py`、`tests/test_agent_chat_launch*.py`（按实际文件名收敛）新增「普通成员 + 无 `agent:*` grant + 拥有私属个人助理」的读取/进入/拒绝矩阵；`tests/fixtures/console_i18n_snapshot.json` 同步。
- 不改动：`agent_bindings` 表结构与 `bind_agent` / `set_member_default_agent` 的写入语义；`resolved_default_agent_id` 的优先顺序与只读性质；文件服务数据面的归属校验（由 `fix-private-agent-file-scope` 负责）；租户管理员与平台管理员的既有旁路；`/api/agents` 的默认（管理）响应契约。

### 数据唯一归属

所有权的唯一来源仍是 `agent_bindings.private_owner_user_id`。本 change **不新增**用户级 grant 表或列：授权判定从既有绑定派生，不产生第二份真相。角色 grant 与租户 grant 的语义不变。

### 跨 change 依赖

- `add-tenant-default-agent-selection`（未归档）：其 `agent-chat-launch` 增量已要求个人默认层的可到达性包含「其为该主体本人的私属资源」。本 change 提供使该条成立的实际判定；两者改的是同一 capability 的**不同 requirement**，归档时需按块合并。**若该 change 先归档，本 change 的放宽表述应与其合并后的文本对齐**。
- `fix-agent-workbench-failure-reporting`（未归档）：覆盖加**载失败态**的可定位与可自愈；本 change 只覆盖**成功空态**的原因区分，两者改的是同一 capability 的**不同 requirement**。
- `scope-console-reads-to-selected-tenant`（未归档）：改 `agent-workbench` 的「最小读取与身份范围」；本 change 以 ADDED 方式新增 requirement，不与其争用同一块。
- `fix-private-agent-file-scope`（未归档）：负责**文件服务数据面**的归属校验。本 change 不涉及文件读取授权，两者在 `tenant-resource-isolation` 上无重叠（本 change 不改该 capability）。

### 明确不在范围内

- 让普通成员看到租户共享的智能体列表：放宽仍只覆盖「可达的解析默认」，逐个非默认共享智能体仍需显式 grant。
- 所有者编辑自己私属助理的人设或核心文件：`agent.edit` 不放宽；若产品需要，另行立规范。
- 人设别名表对中文口径（如「管理员」）的覆盖：属独立的小修正，不并入本 change。
- 为普通成员自动补写 `agent:<id>` grant 的迁移：与「所有权即授权」的方向相反，明确不采用。
