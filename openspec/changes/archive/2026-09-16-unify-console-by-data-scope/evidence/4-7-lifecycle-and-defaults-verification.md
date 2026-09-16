# 任务 4.7 证据：双角色一致、来源/引用/模板保护，双用户双租户默认与旧客户端兼容

对应 `openspec/changes/unify-console-by-data-scope/tasks.md` 的任务 4.7：

> 验证创建/编辑/启停/删除双角色一致、来源保护、引用冲突、模板依赖拒绝及恢复；验证双用户双租户默认、同值重试、并发设置、供应竞态、旧客户端兼容。

本任务是**验证**任务，实现已在 4.1/4.3/4.4/4.6 完成。本文只做两件事：

1. 为每条性质指出**真正钉住它**的测试，并给出该测试在“性质被移除时会失败”的实证（负向对照：临时改产品代码观察失败，随后还原并校验哈希）；
2. 记录**未通过/缺口**，不粉饰。

负向对照只改过 4 个产品文件，每次都从未改动副本还原，还原后用 `rg` 复核“守卫仍在、无 `NEGATIVE CONTROL` 标记”：

| 文件 | 复核实证 |
| --- | --- |
| `auth/service.py` | `if expected_revision != revision:`（:944）、`WHERE tenant_id=? AND user_id=? AND default_agent_revision=?`（:959）、`WHERE ... AND default_agent_origin IS NULL`（:1039）、`if membership["default_agent_origin"] is not None:`（:948/:1030）均在 |
| `agent/admin.py` | `if agent_id == registry.default_agent_id:`（:946）在 |
| `channel/web/web_channel.py` | `ddc4599f25080886508a9474b255fa896cad6da3ef3844a413c633533e7386ba`，与对照前一致 |
| `agent/deletion_guard.py` | `a8fb18d6fef823793ad9705b73cda031bb8abd62d73193bdc3480d7ca5f553be`，与对照前一致 |

`rg "NEGATIVE CONTROL|SILENCED|if False" auth/ agent/ channel/` 在产品代码中无命中。

> **并发写入说明（影响可复现性，不影响结论）**：本任务执行期间 `auth/service.py` 正被**另一个写入者**同时编辑（先后出现
> `6afcee6c → 86c362f1 → a6c04ccc → af018b22` 四个哈希，新增了与本次无关的 `canonical_menu_id`；其中一次中间态在
> `auth/service.py:142` 抛 `NameError`，使 21:14 那轮 7 文件套件出现 1 项无关失败，随后自行恢复为全绿）。因此本节以
> **时间点 + 当时的文件哈希 + 结果**记录，并对“守卫是否仍在”做 `rg` 复核，而不是只依赖哈希相等。上述 4 个文件在本任务中
> 没有任何**净**改动。

## 0. 最终验收命令与结果

最终一轮（2026-09-15 21:16，`auth/service.py = af018b22…`，其余三文件哈希同上表复核值）：

    $ .venv/bin/python -m pytest tests/test_unified_agent_creation.py \
        tests/test_agent_lifecycle_unified.py tests/test_user_default_agent_selection.py \
        tests/test_user_default_initialisation.py tests/test_agent_admin.py \
        tests/test_agent_workbench.py tests/test_plan_3_1_joint_acceptance.py -q -p no:randomly
    .................................. [100%]
    150 passed in 77.07s (0:01:17)

另有一轮（21:12）把 9 个文件一起跑过（即上面 7 个 + 下面 2 个）：

    $ .venv/bin/python -m pytest ... tests/test_tenant_default_agent_selection.py \
        tests/test_private_agent_delete_conflicts.py -q -p no:randomly
    178 passed in 58.67s

该轮的 `auth/service.py` 为 `6afcee6c…`（当时另一写入者的 `canonical_menu_id` 尚未落盘），与 21:16 那轮合计一致：
7 文件 150 + 另 2 文件 28 = 178。两条本任务主要改动的、不在 7 文件清单内的文件单独跑过（21:15）：

    $ .venv/bin/python -m pytest tests/test_tenant_default_agent_selection.py \
        tests/test_private_agent_delete_conflicts.py -q -p no:randomly
    28 passed in 8.94s        # 16 + 12

逐文件（`-q -p no:randomly`，clean tree；7 项合计 150，9 项合计 178）：

| 文件 | 结果 |
| --- | --- |
| `tests/test_unified_agent_creation.py` | 15 passed |
| `tests/test_agent_lifecycle_unified.py` | 24 passed |
| `tests/test_user_default_agent_selection.py` | 25 passed |
| `tests/test_user_default_initialisation.py` | 23 passed |
| `tests/test_agent_admin.py` | 32 passed |
| `tests/test_agent_workbench.py` | 20 passed |
| `tests/test_plan_3_1_joint_acceptance.py` | 11 passed |
| `tests/test_tenant_default_agent_selection.py` | 16 passed |
| `tests/test_private_agent_delete_conflicts.py` | 12 passed |

## A. 双角色一致（管理员 owner 与普通 owner 同类对象同规则）

**性质**：租户管理员与普通成员的**本人私有** Agent，在编辑/配置/启停/删除上走同一份字段、同一份表单、同一份判据；停用对象留在管理列表里可再启用，但不可聊天。

**判据位置**：`auth/object_scope.py:allows_agent`（归属先于管理员例外）、`channel/web/web_channel.py:_tenant_agents_admin_projection`（管理列表字段与 `can_chat`/`unavailable_reason`）。

**钉住它的测试**（`tests/test_agent_lifecycle_unified.py`）：

| 测试 | 断言的可观测事实 |
| --- | --- |
| `MaintenanceParityTests::test_both_owner_roles_edit_their_own_object_the_same_way` | 两种角色的同一 `update` 都 200 且返回同一形状的 `result` |
| `MaintenanceParityTests::test_both_owner_roles_see_the_same_fields_for_their_own_object`（本任务新增） | 两行的**键集合相等**——判据不在值上，而在“同一条投影” |
| `MaintenanceParityTests::test_the_every_object_predicate_is_ownership_first_for_both_roles` | 私有对象：owner 在 `is_admin` 两值下都通过，非 owner 在 `is_admin` 两值下都拒绝；共享对象：`is_admin` 只影响 `manage` 半边 |
| `MaintenanceParityTests::test_the_owner_needs_no_resource_grant_for_their_own_object` | 前置证明 `member` 角色没有任何 `agent:<id>` grant，成功只能来自归属短路 |
| `MaintenanceParityTests::test_another_members_private_object_stays_closed_to_an_admin` / `test_a_third_member_cannot_edit_an_unrelated_private_object` | 管理员与第三方成员都不能借角色改写他人私有对象 |
| `MaintenanceParityTests::test_enable_toggle_is_symmetric_and_keeps_the_object_manageable` | 停用后仍 `enabled=false` 出现在管理列表、`can_chat=false`、`unavailable_reason="agent_disabled"`；再启用恢复 |

**“同一份表单”**：控制台侧由 `channel/web/static/js/console.js` 的同一表单渲染该投影，`personal-console.js` 不参与（设计 D1）；Python 侧可钉的是投影形状，因此上面的键集合测试就是这一半的实证。

**负向对照 1（字段形状按角色分叉）**：在 `_tenant_agents_admin_projection` 里对 `is_tenant_admin` 走一条丢字段的支路（`data.pop("knowledge_mode")`）：

    FAILED tests/test_agent_lifecycle_unified.py::MaintenanceParityTests::
        test_both_owner_roles_see_the_same_fields_for_their_own_object
    E  AssertionError: Items in the first set but not the second: 'knowledge_mode'
    1 failed, 6 passed

**负向对照 2（停用对象仍可聊天）**：把该投影的 `can_chat`/`unavailable_reason` 强制成 `True`/`None`：

    FAILED tests/test_agent_lifecycle_unified.py::MaintenanceParityTests::
        test_enable_toggle_is_symmetric_and_keeps_the_object_manageable
    1 failed, 6 passed

（第一次负向对照写在了赋值**之前**，被随后的赋值覆盖，测试通过——这正是“对照本身也要验证”的例子；改成写在赋值之后就失败了，如上。）

## B. 来源保护（系统供应对象不可删；`unknown` 视为系统来源）

**性质**：删除系统供应的 Agent 被拒绝；`unknown`（早于该列的历史行）同样按系统来源拒绝；拒绝必须是真拒绝，而不是静默 no-op。

**判据位置**：`channel/web/web_channel.py:552 _require_deletable_provenance`（缺列时 `str(binding.get("origin") or "unknown")`，即 `unknown` 默认落在保护集内）+ `auth/service.py:264 SUPPLIED_ASSISTANT_ORIGINS = {"provisioned_assistant", "unknown"}`。

**钉住它的测试**（`tests/test_agent_lifecycle_unified.py::ProvenanceProtectionTests`）：

| 测试 | 断言 |
| --- | --- |
| `test_a_members_supplied_assistant_survives_their_delete` | 403 + `code="forbidden"`，绑定与名册行都还在 |
| `test_an_admins_supplied_assistant_is_protected_the_same_way` | “等级不是判据，来源才是” |
| `test_an_unknown_origin_object_is_supplied_for_deletion_too` | 先断言 `"unknown" in SUPPLIED_ASSISTANT_ORIGINS`，再以 `origin="unknown"` 绑定 + 名册行触发 403 + `code="forbidden"`，绑定与名册行存活 |
| `test_a_self_created_object_is_deletable_by_its_owner` | 反例：保护是窄的，不是全面冻结 |

**负向对照**：把 `"unknown"` 从 `SUPPLIED_ASSISTANT_ORIGINS` 移除：

    FAILED tests/test_agent_lifecycle_unified.py::ProvenanceProtectionTests::
        test_an_unknown_origin_object_is_supplied_for_deletion_too
    1 failed, 3 passed

## C. 引用冲突（渠道实例 / 运行中任务 → 409 冲突清单，不自动改绑，失败关闸，可恢复）

**性质**：删除仍被渠道实例或运行中任务引用的 Agent 被拒绝，返回机器可读冲突清单（HTTP 409、`code:"conflict"`、`conflicts` 列表）；不得静默解绑渠道；探测失败必须 fail-closed；引用解除后同一删除成功。

**判据位置**：`agent/deletion_guard.py:deletion_conflicts`（单一权威，聚合名册渠道、身份库渠道、运行态三类，任一读不到 → 记为冲突）、`channel/web/web_channel.py:590 _require_agent_deletable`。

**钉住它的测试**：

| 测试 | 断言 |
| --- | --- |
| `test_agent_lifecycle_unified.py::ReferenceProtectionTests::test_a_channel_route_refuses_the_delete_and_names_it` | 名册渠道 → 409、`conflict`、`conflicts` 指名 `feishu-ops` |
| `...::test_the_refusal_leaves_both_stores_untouched` / `...::test_the_roster_file_survives_a_refused_delete` | 拒绝**不改绑**：渠道绑定仍是原对象（规范禁止的自动回落没有发生） |
| `...::test_an_administrators_object_meets_the_same_conflict` | 管理员同样 409 |
| `...::test_unlinking_first_lets_the_same_delete_through` | 先解绑 → 同一删除 200（可恢复） |
| `...::test_a_tenant_channel_row_refuses_the_delete_too`（本任务新增） | 身份库 `tenant_channel_instances` 行 → 409、`conflict`、清单含 `Tenant Bot`（feishu, user）；删行后同一删除 200 |
| `...::test_a_channel_routing_to_another_agent_does_not_block` | 反例：不误报 |
| `...::test_the_agent_service_refuses_the_same_delete_on_its_own` | 平台 API / 个人维护路径直调的 `AgentAdminService.delete_agent` 同样 `AgentInUseError(code="conflict")` |
| `...::test_a_running_task_refuses_the_delete_and_names_it`（本任务新增） | 运行中任务（`bridge_has_live_agent`）→ 409、清单含 `running`；清除后同一请求 200 |
| `test_private_agent_delete_conflicts.py::test_an_unreadable_channel_store_fails_closed` | 渠道存储抛错 → 冲突（“查不到”不读作“没有引用”） |
| `test_private_agent_delete_conflicts.py::test_a_probe_failure_fails_closed` | 运行态探测抛错 → 冲突 |
| `test_private_agent_delete_conflicts.py::test_removing_the_reference_lets_the_delete_through` | 可恢复 |

**负向对照 1（引用检查整体失效）**：让 `deletion_conflicts` 直接 `return []`：

    FAILED ReferenceProtectionTests::test_a_channel_route_refuses_the_delete_and_names_it
    FAILED ReferenceProtectionTests::test_a_running_task_refuses_the_delete_and_names_it
    FAILED ReferenceProtectionTests::test_a_tenant_channel_row_refuses_the_delete_too
    FAILED ReferenceProtectionTests::test_an_administrators_object_meets_the_same_conflict
    FAILED ReferenceProtectionTests::test_the_agent_service_refuses_the_same_delete_on_its_own
    FAILED ReferenceProtectionTests::test_the_refusal_leaves_both_stores_untouched
    FAILED ReferenceProtectionTests::test_unlinking_first_lets_the_same_delete_through
    7 failed, 2 passed        # 通过的两条正是“不误报”的负例

**负向对照 2（fail-closed 变 fail-open）**：把 `runtime_is_live` 的 `except → True` 与 `_tenant_channel_references` 的 `except → None` 都改成“当作没有引用”：

    FAILED test_private_agent_delete_conflicts.py::ChannelReferenceConflictTests::
        test_an_unreadable_channel_store_fails_closed
    FAILED test_private_agent_delete_conflicts.py::RuntimeConflictTests::
        test_a_probe_failure_fails_closed
    2 failed, 10 passed

## D. 模板依赖拒绝及恢复

**性质**：模板仍被依赖时拒绝删除，依赖消失后删除成功。

**实际判据**（先说清事实，再断言）：仓库里**没有**“某对象由它克隆而来所以不能删”的规则。唯一存在的“模板”语义是**变量默认 Agent**，判据在 `agent/admin.py:946`：

```python
if agent_id == registry.default_agent_id:
    raise AgentAdminError("the default agent cannot be deleted")
```

`agent_bindings.cloned_from_agent_id` 只用于克隆幂等（来源记录），不是依赖判据。因此“模板依赖”= “该 Agent 当前是变量默认 Agent”。

**钉住它的测试**（`tests/test_agent_lifecycle_unified.py::InstanceTemplateTests`）：

| 测试 | 断言 |
| --- | --- |
| `test_the_instance_template_cannot_be_deleted` | 先**把它绑定到租户**（否则会被更早的 `agent.edit` 门以另一种 403 拦住，测试会“因为错误的原因通过”），再断言 `status="error"` 且消息含 `default agent cannot be deleted`，名册行仍在 |
| `test_the_instance_template_refusal_ends_when_it_stops_being_one` | 把实例默认改到别的 Agent（`service.update_agent("shared-agent", make_default=True)`）后，同一删除 200 且对象已从名册移除——拒绝是**角色**，不是永久冻结 |

**负向对照**：删掉 `agent/admin.py` 里那两行判据：

    FAILED tests/test_agent_lifecycle_unified.py::InstanceTemplateTests::
        test_the_instance_template_cannot_be_deleted
    1 failed, 1 passed

## E. 双用户双租户默认

**性质**：同租户两个成员可同时持有不同个人默认；同一 Agent 若绑定到两个租户，只对“选了它的那个租户”是默认；A 租户成员不能影响 B 租户的解析。

**钉住它的测试**：`tests/test_user_default_agent_selection.py::test_two_tenants_resolve_their_own_defaults_and_preferences`

- A 租户 alice 选 `alice-agent`、A 租户任命租户默认 `shared-agent`；B 租户另一成员选 `glenda-agent`；
- `resolve_default_agent(A, alice) == {"alice-agent","user"}`、`(B, glenda) == {"glenda-agent","user"}`；
- `tenant_default_agent_id(B) is None`——A 的任命不会变成 B 的租户默认；
- A 的成员写 B 的偏好 → `not_found`（主体是 `(tenant, user)`，不是租户可用性）；
- 两成员各自的 `default_agent_id` 各自保留。

**关于“同一 Agent 绑定两个租户”这一字面场景**：`auth/store.py:180` 的 `agent_bindings.agent_id` 是 `TEXT PRIMARY KEY`——**一个 Agent 全局只有一行绑定，跨租户重复绑定在结构上被禁止**。测试末尾显式断言第二次 `bind_agent(tenant_id=B, agent_id="alice-agent")` 抛 `code="conflict"`。因此该子句的结论是“**不可能发生**（由主键保证），并且再次绑定会被拒绝”，而不是“未验证”。

**负向对照**：让 `resolve_default_agent` 忽略成员自身指针（`if user_id:` → `if False:`）：

    FAILED tests/test_user_default_agent_selection.py::
        test_two_tenants_resolve_their_own_defaults_and_preferences
    1 failed

## F. 同值重试（幂等）

**实现承诺**（先读代码再断言，`auth/service.py:833 set_user_default_agent`）：目标与当前值相同时，**在重跑全部资格判定之后**提前返回：

- 不报错（返回同一 `default_agent_id`）；
- `changed: False`；
- **不推进** `default_agent_revision`（客户端刚读到的 revision 仍然有效）；
- **不写审计**（不产生第二次 `member.set_default_agent`）。

租户侧承诺（`auth/service.py:_appoint_tenant_default_agent` + 规范 `tenant-default-agent-administration`）：目标已是当前默认时重复提交成功且不产生额外变更。

**钉住它的测试**：

| 测试 | 断言 |
| --- | --- |
| `tests/test_user_default_agent_selection.py::test_retrying_the_same_target_is_idempotent` | `changed is False`、revision 仍为 2、审计条数不变；且资格判定确实重跑（“幂等不是绕过”） |
| `tests/test_tenant_default_agent_selection.py::TenantDefaultAgentHttpTests::test_set_default_is_idempotent` | 两次 `set_default` 都 200，指针不变 |

**负向对照**：去掉“同目标提前返回”这一支（`if membership["default_agent_id"] == agent_id:` → `if False:`）：

    FAILED tests/test_user_default_agent_selection.py::test_retrying_the_same_target_is_idempotent
    1 failed, 24 passed        # 第二次重试推了 revision、写了审计

## G. 并发设置

**用户侧（乐观锁 `memberships.default_agent_revision`）**：两个并发写同一个 membership 的默认，一个成功、另一个 `version_conflict`，不是静默 last-write-wins。

| 测试 | 断言 |
| --- | --- |
| `tests/test_user_default_agent_selection.py::test_a_lost_race_has_exactly_one_winner` | 同一读出的 revision 上的两次请求 → `[200, 409]`，指针等于胜者，revision == 2 |
| `tests/test_user_default_agent_selection.py::test_two_overlapping_choices_cannot_both_win` | 真并发：`threading.Barrier(2)` + 两个线程同时从 revision 1 出发 → 结果为 `["ok","version_conflict"]`，指针等于胜者，revision == 2 |
| `tests/test_user_default_agent_selection.py::test_a_stale_revision_is_refused` / `test_a_choice_without_a_revision_cannot_overwrite_a_registered_one` | 陈旧 revision / 无 revision 覆盖已登记偏好 → 409 `version_conflict` |

**负向对照（先修好对照本身）**：把**两处**锁同时去掉（`if expected_revision != revision` → `if False:`，并把守卫 UPDATE 的 `AND default_agent_revision=?` 连同第 4 个绑定参数一起删掉）：

    FAILED tests/test_user_default_agent_selection.py::test_two_overlapping_choices_cannot_both_win
    E  assert ['ok', 'ok'] == ['ok', 'version_conflict']
    FAILED tests/test_user_default_agent_selection.py::test_a_lost_race_has_exactly_one_winner
    E  assert [200, 200] == [200, 409]
    2 failed

（这同时证明 `test_two_overlapping_choices_cannot_both_win` 的两个线程**确实重叠**：去掉锁后两次都提交成功。第一次对照只删了 `WHERE` 而没删绑定参数，得到的是 `sqlite3.ProgrammingError`，属于“对照本身写坏了”，已作废重做。）

**租户侧——与任务原文不一致，按规范记录为有意的不对称**：`appoint_tenant_default_agent` **没有**版本参数，`tenants.version` 属于租户编辑器草稿链（`_appoint_tenant_default_agent` 的 docstring 明确“故意不推进租户 version”），规范 `tenant-default-agent-administration` 只要求“幂等的显式动作 + 仅管理员 + 仅绑定目标”，**没有并发场景**；设计 D4 把版本锁放在**用户偏好**指针上，验收项也只列“偏好并发”。

因此租户侧并发是“串行化后后写胜出”。本任务的测试**不把这一现状当成需求来钉**，只钉规范确实要求的、且在加锁后依然成立的不变量
（`tests/test_tenant_default_agent_selection.py::test_overlapping_tenant_appointments_leave_one_legal_audited_value`）：

- 两次任命都跑完，且每个结果要么提交成功、要么是 409（不会出现别的失败）；
- 至少一次提交成功；
- 指针最终是**恰好一个合法绑定目标**（不会撕裂、不会变成外部值）；
- 审计增量 == 成功提交数，即“丢失更新”在审计里可复原，而不是不可见。

**负向对照**：把任命的审计 action 改掉（成功写入但不落 `tenant.set_default_agent`）：

    FAILED tests/test_tenant_default_agent_selection.py::TenantDefaultAgentHttpTests::
        test_overlapping_tenant_appointments_leave_one_legal_audited_value
    FAILED tests/test_tenant_default_agent_selection.py::TenantDefaultAgentHttpTests::test_set_default_is_audited
    2 failed

## H. 供应竞态（只初始化空偏好，first writer wins）

**性质**：供应只在偏好为空时写入；成员已选则供应不覆盖；供应先到则成员的后续选择必须能覆盖，并被标为成员自己的选择（`default_agent_origin`）。

**判据位置**：`auth/service.py:967 initialize_member_default_agent` —— “是否已登记”与“写入”是**同一条**带 `WHERE ... AND default_agent_origin IS NULL` 的 UPDATE（**双层保护**：先读 origin 的提前返回 + 守卫 UPDATE），`BEGIN IMMEDIATE` 内提交。

**钉住它的测试**（`tests/test_user_default_initialisation.py::ProvisioningInitialisesOnlyOnceTests`）：

| 测试 | 断言 |
| --- | --- |
| `test_provisioning_registers_the_assistant_it_just_made` | 空偏好被登记，`origin="provisioned"`，revision 推进到 2（控制台读到的值仍然当前） |
| `test_a_rerun_never_relabels_a_choice_the_member_made` | 成员先选 `alice-second`（origin → `user`），供应再跑 → `skipped/already_registered`，指针仍是成员的选择、origin 仍是 `user` |
| `test_a_provisioning_rerun_leaves_the_registration_alone` | 重跑整行字节不变（“没变更就不写”） |
| `test_the_initialisation_is_first_writer_wins_not_priority_ordered` | 判据是“是否为空”，不是“谁更有优先级”；否则每次重试都会改行 |
| `test_initialisation_refuses_an_agent_of_another_tenant` | 跨租户目标 404，不写入 |
| `test_the_initialisation_is_audited_only_when_it_writes` | 只有真写入才落审计 |
| `test_the_member_can_still_overwrite_a_provisioned_default`（本任务新增） | **反方向**：供应先胜（`origin="provisioned"`），成员随后带 revision 选择 → 指针改为成员的选择、`origin="user"`、`resolve_default_agent` 报 `source="user"`；供应再跑仍 `skipped`，拿不回指针 |

**负向对照**：同时去掉两层保护（提前返回 `if ... is not None:` → `if False:`，并移除 UPDATE 的 `AND default_agent_origin IS NULL`）：

    FAILED ...::test_a_rerun_never_relabels_a_choice_the_member_made
    FAILED ...::test_the_member_can_still_overwrite_a_provisioned_default
    FAILED ...::test_a_provisioning_rerun_leaves_the_registration_alone
    FAILED ...::test_the_initialisation_is_first_writer_wins_not_priority_ordered
    FAILED ...::test_the_initialisation_is_audited_only_when_it_writes
    5 failed, 18 passed

## I. 旧客户端兼容（`set_default` 仍是受管理资格保护的租户动作）

**性质**：旧 `set_default` 仍是租户默认动作，服务端要求管理资格，且拒绝私有目标；不知道 `set_user_default` 的旧客户端继续可用。

**判据位置**：`channel/web/web_channel.py:10614`（`action == "set_default"` → 要求 `ctx.is_platform_admin or ctx.is_tenant_admin`，随后 `appoint_tenant_default_agent`；`private_agent_not_shareable` 由服务端在 `_appoint_tenant_default_agent` 抛出）。任务 4.5 要求新增的是**另一个**动作 `set_user_default`（`web_channel.py:10642`），旧动作语义不变。

**钉住它的测试**（`tests/test_tenant_default_agent_selection.py::TenantDefaultAgentHttpTests`，全部走真实 `POST /api/agents`）：

| 测试 | 断言 |
| --- | --- |
| `test_set_default_changes_the_tenant_default_and_the_resolution` | 200；返回体带回租户当前默认；`tenant_default_agent_id`/`resolved_default_agent_id` 同步变化 |
| `test_set_default_requires_an_administrator` | 普通成员 403，租户默认不变（普通成员不得用旧动作改租户默认） |
| `test_set_default_rejects_an_agent_the_tenant_does_not_own` | 未绑定目标 404，默认不变 |
| `test_set_default_refuses_a_private_agent_and_spares_others` | 私有目标 409 `private_agent_not_shareable`，且**不清空** `private_owner_user_id`（旧动作不得偷偷把私有对象公有化） |
| `test_set_default_is_idempotent` / `test_set_default_is_audited` | 见 F |
| `test_set_default_writes_the_tenant_entry_and_no_user_preference`（本任务新增） | **旧请求不被静默改解释**（设计 D4）：指针落到 `tenants.default_agent_id`，而**每一行 membership 的 `default_agent_id`/`revision`/`origin` 快照逐字不变** |

**负向对照**：把路由的 `set_default` 改接到 `set_user_default_agent`（即“旧请求被静默解释成用户偏好”）：

    FAILED ...::test_set_default_changes_the_tenant_default_and_the_resolution
    FAILED ...::test_set_default_is_audited
    FAILED ...::test_set_default_is_idempotent
    FAILED ...::test_set_default_refuses_a_private_agent_and_spares_others
    FAILED ...::test_set_default_writes_the_tenant_entry_and_no_user_preference
    5 failed, 11 passed

## 未通过/缺口

1. **任务原文“租户默认并发设置同样处理”不成立（有意为之，非缺陷）**。`tenants.default_agent_id` 上没有乐观锁：`appoint_tenant_default_agent` 无版本参数，两窗口同时任命时后写胜出（先写仍被审计）。**规范基线不要求此项**——`tenant-default-agent-administration` 只要求“仅管理员、仅绑定目标、幂等、私有目标拒绝、成功审计”，设计 D4 明确把版本锁定义为**用户偏好**的 `memberships.default_agent_revision`，`tenants.version` 归租户编辑器草稿链。因此本条按“**已显式记录的范围外差异**”处理，测试只钉规范确实要求的可恢复不变量（合法目标 + 成功必审计），已在 §G 记录。若要与任务原文严格对齐，需要给 `tenants` 增加独立 revision 列并让控制台回传——属设计级改动，不在 4.7 的验证范围内。
2. **“同一 Agent 绑定两个租户”在结构上不可表示**（`agent_bindings.agent_id` 为主键）。已改为验证“再次绑定被拒绝”的负例，见 §E。这不是缺口，是数据模型保证。
3. **仓库没有“克隆来源模板依赖”规则**。唯一模板判据是“变量默认 Agent 不可删”（`agent/admin.py:946`）；`cloned_from_agent_id` 仅用于幂等。已在 §D 明确写出，未凭空发明判据。

除上述三条已显式记录的项外，A–I 每一项都有在性质被移除时会失败的测试，且失败已用负向对照实证。

## 本任务新增/修改的测试

| 文件 | 变更 | 钉住的性质 |
| --- | --- | --- |
| `tests/test_agent_lifecycle_unified.py` | 新增 `MaintenanceParityTests::test_both_owner_roles_see_the_same_fields_for_their_own_object` | A（同字段/同一投影） |
| 同上 | 修改 `MaintenanceParityTests::test_enable_toggle_is_symmetric_and_keeps_the_object_manageable`：补 `can_chat=false` / `unavailable_reason="agent_disabled"` 断言 | A（停用对象可管理但不可聊天） |
| 同上 | 新增夹具 `add_roster_entry`、`route_tenant_channel_to`、`drop_tenant_channel` | B/C |
| 同上 | 修改 `ProvenanceProtectionTests::test_an_unknown_origin_object_is_supplied_for_deletion_too`、`test_a_members_supplied_assistant_survives_their_delete` | B |
| 同上 | 新增 `ReferenceProtectionTests::test_a_tenant_channel_row_refuses_the_delete_too` | C（身份库渠道引用，含可恢复） |
| 同上 | 新增 `ReferenceProtectionTests::test_a_running_task_refuses_the_delete_and_names_it` | C（运行态引用，含可恢复） |
| 同上 | 改写 `InstanceTemplateTests::test_the_instance_template_cannot_be_deleted`（先绑定到租户，使失败只可能来自模板规则本身）+ 新增 `test_the_instance_template_refusal_ends_when_it_stops_being_one` | D（含恢复） |
| `tests/test_user_default_agent_selection.py` | 新增 `test_two_tenants_resolve_their_own_defaults_and_preferences` + `_World` 增 `other_member`、`stored(..., tenant_id)` | E |
| 同上 | 新增 `test_two_overlapping_choices_cannot_both_win` | G（真并发） |
| `tests/test_user_default_initialisation.py` | 新增 `test_the_member_can_still_overwrite_a_provisioned_default` | H（反方向：供应先胜，成员后胜并改标 origin） |
| `tests/test_tenant_default_agent_selection.py` | 新增 `test_set_default_writes_the_tenant_entry_and_no_user_preference` + 夹具 `_membership_defaults` | I（旧请求不被静默改解释） |
| 同上 | 新增 `test_overlapping_tenant_appointments_leave_one_legal_audited_value` | G 租户侧（记录不对称，钉规范要求的不变量） |
| 同上 | 新增 `test_set_default_refuses_a_private_agent_and_spares_others`（旧动作拒绝私有目标且不清空 owner） | I |
