# 2.5 范围一致性验收演练（阶段 2 出口）

本文件记录 `tasks.md` 2.5：用**新旧接口**一起验证跨用户/跨租户拒绝、不可见统计、伪造归属、提交时
撤权、版本冲突、迁移中断重试与审计失败回滚。新增用例在
`tests/test_scope_consistency_acceptance.py`（20 项），全部驱动真实 WSGI 应用（gate → resolver →
身份库 → 业务层），不是对谓词的复述。

## 1. 演练场景 → 用例映射

| 要求（`unified-console-access`） | 用例 | 断言要点 |
|---|---|---|
| 跨用户拒绝（读） | `test_a_members_management_list_is_their_own_private_agents`、`test_the_shared_agent_is_usable_by_both_members`、`test_another_members_private_agent_does_not_appear_even_as_an_identifier` | 管理列表 = 本人私有（`tenant=T AND owner=U`）；共享智能体出现在 `?view=workbench`（使用范围）而不是管理列表；三个投影（默认 / `workbench` / `personal`）的响应体里都不出现 `bob-agent` 这个标识符 |
| 不可见统计 | 同上 | 列表长度即调用者可管理/可读的行数，不用越权行填充；`empty_reason` 只区分「本租户没有」与「你没有可用的」，不携带任何标识符 |
| 跨用户拒绝（写） | `test_a_member_cannot_edit_another_members_private_agent`、`test_a_member_cannot_delete_another_members_private_agent`、`test_a_member_cannot_make_another_members_agent_shared` | 403，且**写入未到达 roster**（`world.written == []`）、owner 未变 |
| 非 owner 管理员 | `test_the_tenant_admin_administers_the_shared_surface_not_the_private_one`、`test_the_tenant_admin_cannot_edit_a_members_private_agent` | 管理员的列表只有共享智能体；编辑成员私有智能体 403 且未写 roster（owner 检查先于管理员例外） |
| 正向对照（非空断言） | `test_the_owner_still_edits_their_own_private_agent`、`test_a_body_cannot_move_the_target_to_another_tenant` | 本人编辑成功并到达 roster；带伪造 `tenant_id` 的请求成功但 roster 写入**不含** `tenant_id`，绑定仍属会话租户 |
| 跨租户拒绝 | `test_a_foreign_member_cannot_select_this_tenant`、`test_a_foreign_member_never_receives_this_tenants_agents`、`test_an_agent_of_another_tenant_is_not_addressable` | 选择非成员租户 403/404；即使以自己租户身份请求，载荷中不出现 `alice-agent` / `shared-agent` |
| 提交时撤权 / 目标易主 | `test_a_write_refuses_when_the_object_stopped_being_the_callers`、`test_a_write_refuses_when_the_functional_permission_was_withdrawn` | 读页时对象还归本人；提交前被转为租户共享、或角色被移除 `agent.edit`，保存即 403 且无部分变更 |
| 读范围 ≠ 写范围 | `test_the_read_range_is_not_the_write_range` | 成员可用 `shared-agent`（`read`/`use` grant），编辑仍 403 |
| 正式页面资格 ≠ 管理资格 | `test_an_open_console_does_not_open_role_management`、`test_a_public_edit_grant_is_not_management_qualification` | 见 2.2 证据 |
| 迁移中断重试 | `test_a_failed_migration_leaves_no_partial_state_and_is_retried` | 半途抛错的迁移：其先前 DDL 被回滚（`partial_state` 表不存在）、版本号未记录、`schema_migrations` 无重复；换成可用版本后下一次打开完成 |
| 审计失败回滚 | `test_an_identity_write_rolls_back_when_its_audit_event_fails` | 审计写入抛错时 `set_member_default_agent` 抛错、默认指针**保持原值**、审计条数不增（同事务） |
| 版本冲突 | 见 §3 | 既有套件 |

## 2. 夹具说明（为什么写入被替换）

`AgentsHandler.GET` 在数据库模式读身份投影（真实），但 `POST` 经过
`AgentAdminService(<data root>/config.json)`，而 `get_data_root()` 解析到**本仓**，真实保存会改写
开发者的 `team.json`。因此验收夹具用记录器替换 `_agent_admin_service`：被拒绝的保存必须让记录器
为空（这正是被测性质），被允许的保存必须到达记录器（正向对照），两侧都成立才算证据。

## 3. 版本冲突证据（既有）

```
.venv/bin/python -m pytest \
  "tests/test_builtin_role_editing.py::BuiltinGuardTests::test_version_conflict_still_applies_to_builtin" \
  "tests/test_agent_admin.py::test_a_stale_roster_revision_is_refused" -q
.venv/bin/python -m pytest tests/test_identity_service_writes.py -q          # profile version conflict
.venv/bin/python -m pytest tests/test_identity_web_handlers.py -q            # tenant rename version conflict
.venv/bin/python -m pytest tests/test_tenant_channel_instances_service.py -q # stale version changes nothing
```

## 4. 阶段 2 可复跑汇总

```
.venv/bin/python -m pytest \
  tests/test_object_scope.py tests/test_user_default_migration.py \
  tests/test_console_menu_mapping.py tests/test_scope_consistency_acceptance.py \
  tests/test_agent_workbench.py tests/test_private_agent_owner_reachability.py \
  tests/test_memory_console_scope.py tests/test_agent_admin.py \
  tests/test_private_agent_lifecycle.py tests/test_private_agent_capability_save.py \
  tests/test_private_agent_integration.py tests/test_tenant_channel_instances_service.py \
  tests/test_builtin_role_editing.py tests/test_http_policy.py \
  tests/test_rbac_tenant_constraints.py tests/test_identity_service_writes.py \
  tests/test_identity_web_handlers.py -q
→ 486 passed, 3 subtests passed (88s)
```

分文件：`test_object_scope` 20、`test_user_default_migration` 21、
`test_console_menu_mapping` 29、`test_scope_consistency_acceptance` 20。

## 5. 进入阶段 3 的判定

上表全部场景有可复跑证据，无未决失败；阶段 2 出口达成，可以开始
`tasks.md` 3.x（移除控制台入口的管理员专属准入、把普通用户接入现有页面、删除「我的资源」菜单）。

阶段 2 **未**声称覆盖的部分（按 `tasks.md` 的既有阶段划分，且不因归档状态视为验收）：

- 渠道的页面级统一（6.x/7.x）与记忆/工具/技能/模型共用页面（5.x）——本阶段只收敛其背后的授权层；
- 真实提供方渠道往返、原生 Desktop 一致性：仍以各阶段自己的真实验收为准
  （见 `evidence/1-4-prerequisite-slices.md`）。
