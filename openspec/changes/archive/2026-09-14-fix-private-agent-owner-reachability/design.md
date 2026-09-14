## Context

动机与实测证据见 `proposal.md`。此处只记录塑造方案的在位实现与约束。

当前判定分散在三处，且互不知情：

- **授权解析**：`auth/service.py` 的 `resource_ids_for`（`1291`）与 `check_resource_action`（`1209`）是**纯角色/租户 grant 匹配**，不含任何所有权分支；`auth/policy.py` 的 `resource_ids_for`（`450`）是它们共用的无状态函数，拿不到绑定信息。
- **投影过滤**：`channel/web/web_channel.py` 的 `_iter_tenant_agents`（`8293`）用 `allowed_agent_ids`（经 `_resource_ids`，`355`）+ 单一放宽谓词 `_tenant_shared_default_agent`（`411`）决定可见性；租户管理员在 `8314` 被整体豁免。
- **就绪与发送**：`_workbench_chat_readiness`（`8407`）与发送路径复用同一个放宽谓词，因此两边结论天然一致。

关键耦合点：`_resolve_tenant_default_agent`（`8242`）对普通成员带 `subject=user_id`，落到 `resolved_default_agent_id`（`627`）的第 1 层——该成员的**私属**个人助理会被解析为其 `tenant_default`。于是放宽谓词的两个条件（`agent_id == tenant_default` 且**非私有**）对唯一有希望的候选互斥，可见集为空。

既有约束：`bind_agent`（`746`）与 `set_member_default_agent` 都不写 grant；身份库里只有角色级与租户级 grant 载体，**没有用户级 grant 表**（见 `openspec/changes/fix-private-agent-owner-reachability/proposal.md` 的「数据唯一归属」）。

## Goals / Non-Goals

**Goals:**

- 让「所有权」成为读取与使用的授权来源之一，且只有一份实现，使投影、就绪与发送路径不可能给出不一致结论。
- 使 `add-tenant-default-agent-selection` 已写下的「个人默认层可到达性包含本人私属」在实现上真正成立。
- 让「列表成功但为空」不再掩盖授权/可达性缺陷。

**Non-Goals:**

- 不改 `resolved_default_agent_id` 的优先顺序、只读性质与回落规则。
- 不新增用户级 grant 载体，不为存量成员回填 `agent:<id>` grant。
- 不改变文件服务数据面的归属校验（`fix-private-agent-file-scope` 负责）。
- 不放宽 `agent.edit` / `agent.enable`，也不扩大普通成员对**非默认**共享智能体的可见性。

## Decisions

### 决策 1：所有权判定放在身份服务，而非投影层

在 `auth/service.py` 引入「该主体对某 Agent 是否具备读取/使用资格」的统一判定（可读集合 + 单资源断言两个入口共用），由 `resource_ids_for` 与 `check_resource_action` 同时接入；`channel/web/web_channel.py` 的投影与就绪**消费**该判定，不再自行拼装条件。

- **理由**：`agent-chat-launch` 要求投影与发送路径不得分歧；把规则放在两者共同的下游是唯一能保证这一点的位置。投影层继续自己推导，就会随每次新增消费者而重复出现本缺陷。
- **备选 A（仅改投影层放宽谓词）**：改动最小，但语义键在「解析默认」而非「所有权」——成员一旦不再以该私属为默认（登记被清空、解析回落）就会重新不可见，且发送路径之外的消费者仍不通。
- **备选 B（绑定私属时写入显式 grant）**：与既有授权模型同构，但系统内没有用户级 grant 载体，需新增表或为每人造角色；且绑定与 grant 形成两份真相，删除/停用/迁移时必须同步，风险高于收益。

### 决策 2：放宽按「可达的解析默认」表述，两个分支并列而非嵌套

把 `agent-chat-launch` 的放宽从「租户共享的解析默认」改写为「租户共享的解析默认**或**调用者本人私属的解析默认」两个并列分支，并明确两者 MUST NOT 互相挤占。

- **理由**：本缺陷的直接成因就是「私属默认占用了 `tenant_default`，于是共享分支也不成立」。显式并列并把「不得互相挤占」写成规范约束，才能在回归中被测到。
- **取舍**：对**已有**个人助理的普通成员，其列表将只有自己那一张卡（非默认共享智能体仍需显式 grant）。这是既有规范的既定语义，本 change 不扩大它。
- **实现期修正（口径记录）**：落地时**没有**把两个分支写进同一个谓词，而是**分置两处**——私属支由决策 1 的统一判定承载（所有权即授权），共享支保留原谓词 `_tenant_shared_default_agent`。理由是私属支的正确语义键是「所有权」而非「解析默认」（这正是决策 1 备选 A 被否掉的原因），把它塞回「解析默认」谓词会让可见性重新依赖默认登记状态，与任务 4.8「可见性 MUST NOT 取决于是否为当前解析默认」冲突。两支互不挤占的性质由用例固定（`test_owner_entry_survives_a_different_tenant_shared_default`、`test_shared_default_relaxation_still_applies_without_a_grant`），规范层面的约束文字不变。

### 决策 3：所有权判定必须同时校验租户绑定

判定条件为「绑定的 `private_owner_user_id` 等于调用者**且**绑定 `tenant_id` 等于当前所选租户」。缺一不可。

- **理由**：`private_owner_user_id` 是全库唯一的用户标识，但授权必须仍以租户为隔离边界；只比 owner 会让「同一 Agent 的绑定被重指到另一租户」这类异常绕过租户校验。

### 决策 4：空态返回稳定原因码，而非可枚举细节

工作台投影在结果为空时附加粗粒度原因（「无对象」/「存在但不可达」），并对后者记录服务端告警；原因 MUST NOT 携带不可见对象的标识、名称或数量。

- **理由**：`fix-agent-workbench-failure-reporting` 已让**失败态**可定位，但「成功且空」仍把两种成因压成同一句「暂无可用智能体」。粗粒度原因足以指导使用者与运维，同时不构成存在性枚举面。
- **备选**：返回被过滤掉的智能体标识——直接否，那正是权限泄露。
- **实现期补充（与未归档 change 的争用）**：`fix-agent-workbench-failure-reporting` 正在 MODIFY `agent-workbench` 的「加载空态与失败可区分」，其场景「没有可见智能体」原文把「读取成功但结果为空」一律绑定到「暂无可用智能体」，与本要求冲突。已在本 change 内补一个 `MODIFIED` delta，仅收窄该场景的 WHEN 至「租户确无可读智能体」并声明文案随原因码选择，保留三态划分不变；归档顺序见 `tasks.md` 5.3。

### 决策 5：不改 `/api/agents` 的默认响应契约

新字段只出现在 `view=workbench` 的最小投影上；管理快照与旧选择器的结构、版本冲突语义保持原样。

- **理由**：`agent-workbench` 既有「既有读取接口保持兼容」要求；Desktop 与旧选择器按原结构读取。

## Risks / Trade-offs

- **权限蔓延（所有权被顺带用于写动作）** → 判定按 action 区分，`edit`/`enable` 明确排除；规范内写入「可达 MUST NOT 蕴含可改」场景并加回归用例。
- **跨租户泄露（判定漏掉租户维度）** → 决策 3 的双条件；补「他人私属」「跨租户」两条反向用例。
- **每请求重复加载绑定导致热路径变慢** → `check_resource_action` 在每次工具/Agent 动作上被调用。落地口径：**不引入显式请求级缓存**，改为消除重复查询——集合语义的 `private_agent_ids()` 每个投影只调用一次（`resource_ids_for` 每请求一次），单资源路径走 `get_agent_binding()` 主键单行读取（与既有 `_agent_bound_to_tenant` 同量级）。结论不 memoize，撤销所有权下一次请求即生效，符合 `resource-execution-authorization` 「首期不缓存跨请求授权结论」。
- **与未归档 change 争用同一 capability** → 见 proposal 的「跨 change 依赖」：`agent-chat-launch` 与 `agent-workbench` 各有别的未归档 delta，但都在**不同 requirement 块**上，归档时按块合并；若 `add-tenant-default-agent-selection` 先归档，本 change 的放宽表述需与其合并后的文本对齐。
- **列表只剩一张卡的观感**（决策 2 的取舍） → 属既有语义，本 change 不改；若产品希望成员也看到租户共享入口，需另行立规范而非在此顺带放宽。
- **需求名与内容不再完全一致**：`agent-chat-launch` 中被修改 requirement 的名称仍为「共享默认智能体的可达性…」，而内容已覆盖本人私属分支。改名需走 RENAMED 与 MODIFIED 的组合，为降低归档期合并风险，本 change 保留原名（记为 Open Question）。

## Migration Plan

- **无 schema 迁移、无数据回填**。授权每次请求重新解析，因此修复对存量受影响成员在**下一次请求**即生效；不需要重启进程或重建绑定。
- 不需要为存量成员补写 `agent:<id>` grant（决策 1 的备选 B 已明确不采用）。
- **回滚**：撤销判定接入即可回到当前行为；因未写入任何持久化状态，回滚不遗留脏数据。
- 发布顺序：先服务端判定与投影，再前端文案与 i18n（前端在后端之前发布只会继续显示旧空态，不会产生错误承诺）。

## Open Questions

- 是否把该 requirement 更名为「解析默认智能体的可达性不依赖逐资源授权」：纯命名，不改变行为、方案或任务拆分，可在任意后续 change 中用 RENAMED 处理。
- 「存在但不可达」空态的最终文案措辞（三语系）可在实现期按 i18n 惯例定稿，不影响规范与任务。
