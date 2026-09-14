## 1. 授权判定：所有权作为读取与使用的来源

- [x] 1.1 在身份服务新增统一的「该主体对某 Agent 是否具备读取/使用资格」判定：绑定 `tenant_id` 等于调用者当前所选租户，且 `private_owner_user_id` 等于调用者本人。两者必须同时成立（防止跨租户或重指绑定绕过租户校验）。
  - `IdentityService.is_private_agent_owner()` / `private_agent_ids()`；以 `get_agent_binding()` 主键读取，三条件同时校验。
- [x] 1.2 让「可读资源集合」入口并入调用者本人私属 Agent 的标识，使工作台投影无需自行推导。
  - `resource_ids_for()`：`kind == "agent"` 且 `action in PRIVATE_AGENT_OWNER_ACTIONS` 时并入 `private_agent_ids()`。
- [x] 1.3 让「单资源动作断言」入口对 `read` / `use` 承认所有权，且**不**对 `edit` / `enable` 承认（可达 MUST NOT 蕴含可改）。
  - `check_resource_action()` 的所有权分支只在 `PRIVATE_AGENT_OWNER_ACTIONS = ("read", "use")` 生效（新增于 `auth/policy.py`，与 `RESOURCE_ACTIONS` 同处定义）。
- [x] 1.4 保留平台管理员的 `all` 旁路与租户管理员对本租户绑定 Agent 的既有旁路不变；所有权判定不得改变二者的既有结论。
  - `all` 仍在 `authorization_mode()` 之后立即短路；租户管理员旁路（`_tenant_admin_owns_agent`）未改动。回归由现有套件覆盖。
- [x] 1.5 绑定集合按 `(user_id, tenant_id)` 在**单次请求内**复用，避免热路径（工具/Agent 动作）重复查询；MUST NOT 引入跨请求授权结论缓存，以符合 `resource-execution-authorization` 的既有约束。
  - **实现口径（与初稿不同）**：不引入显式请求级缓存，改为消除重复查询——(a) 集合语义的 `private_agent_ids()` 每个投影只调用一次（`resource_ids_for` 每请求一次）；(b) 单资源路径走 `get_agent_binding()` 主键单行读取，与 `_agent_bound_to_tenant` 同量级；(c) 结论不 memoize，撤销所有权下一次请求即生效（由 `test_revoking_ownership_takes_effect_on_the_next_call` 固定）。不新增任何跨请求缓存。
- [x] 1.6 不新增用户级 grant 表或列，不为存量成员回填 `agent:<id>` grant；所有权继续由 `agent_bindings.private_owner_user_id` 唯一派生。
  - 无 schema 变更、无回填；`private_agent_ids()` 直接派生。

## 2. 投影与就绪接入统一判定

- [x] 2.1 工作台可见性过滤改为消费第 1 组的统一判定，移除投影层自行拼装的放宽条件。
  - **私属支**已完全移出投影层：投影只消费 `_resource_ids(ctx, "agent", "read")`，所有权由第 1 组并入，无需投影层推导。**租户共享支**保留原谓词 `_tenant_shared_default_agent`——它判定的是「解析默认是否为租户共享」，属租户默认语义，服务层无法单独裁决（见 2.3 口径）。
- [x] 2.2 卡片就绪判定与发送路径消费同一判定，确保「卡片可点即发送可通」，不出现投影与发送分歧。
  - 就绪 `_workbench_chat_readiness` 与发送 `_require_agent_action(..., "use")` 均落到同一个 `check_resource_action`；由 `test_readiness_agrees_with_the_projection`、`test_send_path_use_gate_admits_the_owner` 双向固定。
- [x] 2.3 把可达性放宽从「租户共享的解析默认」改写为**并列**两支：租户共享的解析默认，或调用者本人私属的解析默认；确保两支不互相挤占（不得因解析默认恰为本人私属而使该成员入口整体落空）。
  - **实现口径（与初稿不同）**：不把两支塞进同一谓词，而是**分置两处**——私属支由第 1 组的统一判定（所有权即授权）承载，共享支由未改动的 `_tenant_shared_default_agent` 承载。两支互不挤占：先前 `_tenant_shared_default_agent` 的 `agent_id != tenant_default` 直接吞掉私属默认，使成员两端皆空；现在私属支不再依赖该谓词，成员入口必然可达。由 `test_owner_entry_survives_a_different_tenant_shared_default`（租户默认另为共享时本人入口仍在）与 `test_shared_default_relaxation_still_applies_without_a_grant`（共享支不回归）共同固定。
- [x] 2.4 确认放宽边界未扩大：非默认智能体仍需逐资源 grant；他人私属仍被拒；不跨租户匹配。
  - `test_shared_agent_still_needs_a_grant`、`test_other_member_cannot_read_or_use_it`、`test_owner_of_another_tenants_agent_is_not_reachable`、`test_send_path_still_refuses_another_tenants_member`。
- [x] 2.5 确认 `resolved_default_agent_id` 的优先顺序、只读性质与回落规则未被改动。
  - 本 change 未触碰该方法（`git diff` 无此函数改动）；相关既有测试全绿。

## 3. 空态可诊断

- [x] 3.1 工作台投影在**读取成功但结果为空**时回带稳定原因码，区分「租户确无可读智能体」与「存在候选但全部不可达」。
  - `_tenant_agents_projection()` 新增 `empty_reason`；分母由新增的 `_tenant_agent_candidates()`（已绑定 + 已启用，**不**做授权过滤）提供，`no_agents` / `no_reachable_agents`。
- [x] 3.2 原因码为稳定可本地化标识，MUST NOT 携带不可见对象的标识、名称、数量或路径；对「存在但不可达」记录服务端可诊断告警。
  - 载荷只有 `agents: []` 加原因码，无计数、无标识、无路径；`no_reachable_agents` 触发 `logger.warning("[Workbench] ...")`，从**受影响读数**发出（不依赖管理员视角）。由 `test_reason_does_not_leak_the_hidden_agents_identity` 固定。
- [x] 3.3 前端按原因码呈现两种可区分文案，且维持成功态语义：不显示失败提示、不提供重试动作、不修改当前会话归属。
  - `_wbEmptyReason` + `_wbEmptyKey()`；空态仍走 `setWbStatus`（成功态），非 `setWbError`。由 `test_an_empty_list_that_hides_Agents_says_so_instead_of_...` 固定（含无重试按钮、会话归属不变）。
- [x] 3.4 补齐 zh / zh-Hant / en 三语系文案，并同步 `tests/fixtures/console_i18n_snapshot.json`（逐键一致契约）。
  - 新增 `agent_workbench_empty_unreachable` 三语；快照同步，`test_console_i18n_parity.cjs` 全绿。
- [x] 3.5 确认 `/api/agents` 的默认（管理）响应与版本冲突语义不变，新字段只出现在 `view=workbench` 投影上。
  - `empty_reason` 只由 `_tenant_agents_projection` 产出；管理投影 `_tenant_agents_admin_projection` 未改。由 `test_default_view_returns_tenant_admin_projection`（整响应相等）与 `test_view_workbench_empty_response_carries_a_reason_code` 固定。

## 4. 测试

- [x] 4.1 端到端：普通成员（仅持功能权限、无任何 `agent:<id>` grant）拥有本租户私属个人助理时，工作台列表**包含**该助理并标记为默认。
  - `tests/test_private_agent_owner_reachability.py::test_member_sees_own_private_assistant`（真实 `IdentityService` + 临时身份库）。
- [x] 4.2 同一成员可对该助理发起会话：就绪判定为真，且发送路径独立放行（不得只测投影）。
  - `test_member_can_chat_with_own_private_assistant`、`test_readiness_agrees_with_the_projection`、`test_send_path_use_gate_admits_the_owner`（直接调用 `_require_agent_action`，不经投影）。
- [x] 4.3 反向：同租户另一名普通成员看不到、也进不去该私属助理。
  - `test_other_member_cannot_read_or_use_it`、`test_other_member_does_not_see_it`。
- [x] 4.4 反向：另一租户成员请求该助理被拒，且不因同属一个平台放行。
  - `test_owner_of_another_tenants_agent_is_not_reachable`、`test_send_path_still_refuses_another_tenants_member`。
- [x] 4.5 写动作：所有者本人缺少 `edit` / `enable` 资源 grant 时，仍 MUST NOT 能编辑或启停该助理。
  - `test_owner_cannot_edit_own_private_agent_without_grant`、`test_owner_cannot_enable_own_private_agent_without_grant`、`test_edit_set_excludes_own_private_agent`、`test_owner_cannot_edit_without_a_grant`（走 `_require_agent_action`，断言 403）。
- [x] 4.6 回归：成员**没有**个人助理时，租户共享默认对无授权成员的既有无障碍放宽保持不变。
  - `test_shared_default_relaxation_still_applies_without_a_grant`。
- [x] 4.7 回归：非默认共享智能体对无 grant 成员仍不可见可用。
  - `test_shared_agent_still_needs_a_grant`、`test_member_sees_own_private_assistant`（断言共享项不在列表中）。
- [x] 4.8 解耦：个人默认登记缺失或被清空时，成员仍能看到并进入自己的私属助理（可见性 MUST NOT 取决于是否为当前解析默认）。
  - `test_visibility_does_not_depend_on_being_the_default`（把个人默认改指共享项后，私属助理仍在）。
- [x] 4.9 空态：两种空态成因返回不同原因码；断言原因码不含不可见对象的标识、名称、数量或路径。
  - `EmptyReasonTests` 四例（`no_agents` / `no_reachable_agents` / 不泄露 / 非空无原因码）。
- [x] 4.10 前端：空态呈现为成功态、无重试按钮、两种文案可区分；断言不误入失败态分支。
  - `tests/test_agent_workbench_frontend.cjs` 新增四例（不可达文案、无对象文案、原因码随读取清空、旧服务端无原因码回落）。

## 5. 验证、迁移与归档

- [x] 5.1 以真实身份库与受控数据跑完第 4 组矩阵，记录**修复前后对照**证据（含本 change 的复现场景：普通成员 + 私属助理 + 零 `agent:<id>` grant）。
  - 真实 `IdentityService`（临时 `identity.db`）+ `RequestContext`，非 mock。**修复前**：`_tenant_agents_projection()` 返回 `{"agents": []}`（`AssertionError: Lists differ: [] != ['rock-assistant']`）、`check_resource_action(..., "use")` 为 `False`。**修复后**：同输入返回 `agents=[{id: "rock-assistant", is_default: True, can_chat: True}]`，`use`/`read` 放行、`edit`/`enable` 仍拒。共 28 例全绿。
- [x] 5.2 确认无 schema 迁移、无数据回填、无需重启即对存量受影响成员生效；演练回滚路径（撤销判定接入即回到旧行为，且不遗留脏数据）。
  - 无 schema/迁移/回填：本 change 只改 `auth/policy.py`、`auth/service.py`、`channel/web/web_channel.py` 与前端资源。存量成员无需重绑——`private_owner_user_id` 早已写入，只是此前未被用作授权来源。回滚＝撤销这三处判定接入，不产生遗留数据（无新列、无新行、无写路径）。
- [x] 5.3 归档前核对与未归档 change 的 requirement 块不争用：`agent-chat-launch`（`add-tenant-default-agent-selection`）与 `agent-workbench`（`fix-agent-workbench-failure-reporting`、`scope-console-reads-to-selected-tenant`）。若 `add-tenant-default-agent-selection` 先归档，先对齐放宽表述再归档本 change；归档说明记录合并顺序。
  - `agent-chat-launch`：`add-tenant-default-agent-selection` MODIFY 的是「租户恒有可解析的默认智能体」，与本 change 的「共享默认智能体的可达性不依赖逐资源授权」**不同块**，不争用。
  - `agent-workbench`：`scope-console-reads-to-selected-tenant` MODIFY「最小读取与身份范围」**不同块**。但 `fix-agent-workbench-failure-reporting` MODIFY 的「加载空态与失败可区分」其场景「没有可见智能体」原文要求「读取成功但结果为空」一律呈现「暂无可用智能体」，与本 change 的 ADDED 要求**语义冲突**——已在本 change 内补 `MODIFIED` delta（仅收窄该场景的 WHEN 至「租户确无可读智能体」并声明文案随原因码选择），否则两者归档后主规范自相矛盾。
  - **合并顺序**：先归档 `fix-agent-workbench-failure-reporting`，再归档本 change；若无法保证该顺序，归档本 change 前须把其 delta 里的失败定位/渲染隔离条款并入本 change 的 `MODIFIED` 块（该块当前基于主规范的既有简短文本）。归档说明须记录此顺序。
- [x] 5.4 本 change **不引入 feature flag**：它是对既有规范未实现的修复，行为完全由授权判定决定，无「关闭态」语义；MUST NOT 以隐藏菜单、前端隐藏或伪造标识替代服务端判定。
  - 无开关、无环境变量；判定落在身份服务，前端只消费结果（`empty_reason` 亦仅呈现）。
- [x] 5.5 统一 `doc/产品规划.md` 的口径：个人智能体已由产品确认为**当前版本能力**，需把与 3.1.1 冲突的表述下修——3.2 的「当前版本：成员在本租户内创建的智能体归属该租户…个人独有的是个人风格设定、个人记忆与界面偏好」、3.2 的「用户可使用的智能体…由租户管理员授权决定」、5.3 的「用户个人拥有独立资源」行、以及第 3 章末的「落地状态」注记（其把 3.2 的「用户个人拥有独立资源」归入后续版本）。同处顺带澄清两处张力：3.1 「关键规则」第 1 条对租户管理员的可见性（管理职责内可读 vs「分享后才可见」），与第 2 条为「解析默认智能体」补例外说明。核心规则表达优先以 `openspec/specs/` 为准，文档只做口径对齐。
  - 已按上述逐处对齐：3.1.2 关键规则 1/2（含租户管理员可见性边界、私属与解析默认两处例外、「可达不等于可改」）、3.2（个人助理为当前能力、本人可读可用但不可改、分享仍为后续）、3.3 与落地状态注记、5.1 新增「个人智能体」行、5.3 移除「用户个人拥有独立资源」并改列剩余范围；版本 v1.1→v1.2 并追加变更记录。
- [x] 5.6 检查 `docs/` 与 `webhelp/` 中涉及「智能体列表为空」与个人助理可达性的说明，同步到修复后的行为；无对应文档时记录为无需变更。
  - 无文档描述「智能体列表为空」，故空态改动的对外说明只落在规范与 i18n。
  - `docs/design/personal-conversation-and-memory.md` 与 `…-acceptance.md` 确述及私属门禁：**不改写原记录**，各追加一条 2026-09-14 补记，说明「他人私属仍被拒」不变、而「所有者本人此前也被过滤」已修（所有权即 `read`/`use` 授权）。
  - `webhelp/lang/zh.php` 的用户级动作 `创建维护个人智能体、工具与技能` 与 3.1.1 口径一致，个人智能体现属当前能力，**无需变更**。

---

### 遗留（不阻塞本 change）

- 所有者可见其私属助理后，管理页会同时展示其可编辑字段，但 `edit` / `enable` 仍须逐项授权（可达不等于可改）。「看得见、存不了」的呈现需产品裁决后另开 change，本 change 不擅自放宽。
- `tests/test_agent_workbench.py::TestWorkbenchProjection::test_readiness_defaults_to_runnable_in_legacy` 断言的是已退役的 legacy 行为（现为 fail-closed 返回 `unauthorized`），在本 change 之前即已失败，与本次改动无关。
