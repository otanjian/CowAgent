# Evidence — copy-default-tenant-agents

对照 `specs/**/spec.md` 的场景逐条给出实现位置与实测结果。以下测试均为本 change 新增或更新，除注明外全部通过。

执行命令与实测输出（2026-09-10）：

```
$ .venv/bin/python -m pytest tests/test_agent_clone_binding.py tests/test_agent_clone_primitives.py \
    tests/test_tenant_agent_provisioning.py tests/test_tenant_agent_copy_http.py \
    tests/test_tenant_agent_copy_acceptance.py tests/test_http_policy.py -p no:cacheprovider
118 passed, 1 warning in 14.4s

$ node --test tests/test_tenant_tabbed_editor_frontend.cjs tests/test_i18n_tenant_editor_keys.cjs
ℹ tests 67  ℹ pass 67  ℹ fail 0
```

## tenant-agent-provisioning

### Requirement: 源租户解析与复制候选读取

| Scenario | 测试 | 结果 |
| --- | --- | --- |
| 解析到持有智能体的默认租户 | `test_tenant_agent_provisioning.py::SourceResolutionTests::test_source_is_the_tenant_holding_the_global_default_agents_binding`；HTTP：`test_tenant_agent_copy_http.py::test_get_returns_the_target_agents_and_the_source_candidates` | pass |
| 候选标注已复制状态 | `CandidateReadingTests::test_candidates_mark_already_copied_agents_with_their_clone_id`；HTTP：`test_get_marks_already_copied_candidates` | pass |
| 来源无任何绑定智能体 | `CandidateReadingTests::test_reading_reports_an_empty_candidate_set_with_a_reason` | pass |
| 无法确定来源 | `SourceResolutionTests::test_source_is_unresolvable_with_no_agent_bindings`、`CandidateReadingTests::test_reading_reports_an_unresolvable_source_without_raising` | pass |
| 回退到绑定数最多的租户 | `SourceResolutionTests::test_source_falls_back_to_the_tenant_with_the_most_bindings` | pass |
| 读取不含宿主路径 | `CandidateReadingTests::test_candidates_never_expose_host_paths`；HTTP：`test_get_never_leaks_a_workspace_or_host_path` | pass |

### Requirement: 复制以显式选中集合为准

| Scenario | 测试 | 结果 |
| --- | --- | --- |
| 只复制选中的智能体 | `SelectionValidationTests::test_unselected_candidates_are_not_copied`；验收：`test_tenant_agent_copy_acceptance.py::SelectionFidelityTests::test_unselected_candidates_are_absent_from_the_target` | pass |
| 未勾选即提交被拒 | `SelectionValidationTests::test_empty_selection_is_refused_without_writing`；HTTP：`test_empty_selection_is_rejected_without_writing`；前端：`test_tenant_tabbed_editor_frontend.cjs::submitting after unchecking everything sends no request and says what is missing` | pass |
| 选中项不属于来源租户 | `SelectionValidationTests::test_unknown_selection_id_rejects_the_whole_request`；HTTP：`test_an_id_outside_the_candidates_is_rejected_without_writing` | pass |
| 目标=来源被拒 | `GuardTests::test_target_equal_to_source_is_refused`、`test_unknown_target_tenant_is_refused`；HTTP：`test_get_reports_that_a_tenant_cannot_copy_from_itself`、`test_an_unknown_target_tenant_is_rejected` | pass |

### Requirement: 克隆为独立智能体且不共享绑定

| Scenario | 测试 | 结果 |
| --- | --- | --- |
| 生成独立智能体 | `IdentityGenerationTests::test_new_id_and_workspace_are_derived_from_source_and_target`；验收：`IsolationAcceptanceTests::test_the_clone_is_bound_only_to_the_target_tenant` | pass |
| 标识冲突时仍保持唯一 | `IdentityGenerationTests::test_id_collision_appends_a_sequence_number`、`test_overlong_source_ids_are_truncated_to_the_registry_limit` | pass |
| 两侧互不影响 | 验收：`IndependentEvolutionTests::test_editing_the_clone_leaves_the_source_alone`、`test_editing_the_source_leaves_the_clone_alone` | pass |

### Requirement: 复制内容边界

| Scenario | 测试 | 结果 |
| --- | --- | --- |
| 带人设与配置属性 | `test_agent_clone_primitives.py::test_clone_mirrors_the_configuration_but_not_the_identity`、`test_clone_copies_the_persona_and_leaves_runtime_state_behind`、`test_clone_is_registered_and_leaves_the_source_untouched` | pass |
| 不复制记忆与凭据 | `test_clone_copies_the_persona_and_leaves_runtime_state_behind`；验收：`IsolationAcceptanceTests::test_a_clone_carries_the_persona_but_no_credentials_or_history` | pass |
| 知识模式按来源形态复刻 | `test_clone_of_a_shared_knowledge_agent_keeps_sharing`、`test_clone_of_an_own_knowledge_agent_gets_its_own_empty_base`、`test_cloning_the_default_agent_into_its_own_subtree_terminates` | pass |

### Requirement: 来源映射与幂等补缺

| Scenario | 测试 | 结果 |
| --- | --- | --- |
| 重复执行只补缺 | `IdempotencyTests::test_rerunning_the_same_selection_only_skips`；HTTP：`test_post_reports_a_rerun_as_skipped`；验收：`SelectionFidelityTests::test_reopening_the_database_and_rerunning_changes_nothing` | pass |
| 混选已复制与未复制 | `IdempotencyTests::test_mixed_selection_copies_only_the_missing`；验收：`test_a_rerun_neither_duplicates_nor_orphans` | pass |
| 幂等不依赖命名 | `IdempotencyTests::test_skip_survives_renaming_the_clone_in_the_target` | pass |
| 来源映射写入绑定 | `test_agent_clone_binding.py::BindAgentCloneProvenanceTests::test_bind_records_the_source_agent` | pass |

### Requirement: 目标默认智能体承接

| Scenario | 测试 | 结果 |
| --- | --- | --- |
| 空目标承接来源默认 | `DefaultAgentTakeoverTests::test_empty_target_adopts_the_clone_of_the_source_default` | pass |
| 未选来源默认时仍可解析默认 | `DefaultAgentTakeoverTests::test_empty_target_without_the_source_default_uses_the_first_copied` | pass |
| 非空目标不改默认 | `DefaultAgentTakeoverTests::test_non_empty_target_keeps_its_default` | pass |
| 全部跳过时不改默认 | `DefaultAgentTakeoverTests::test_all_skipped_selection_does_not_touch_the_default` | pass |
| 服务端写入默认并审计 | `test_agent_clone_binding.py::SetTenantDefaultAgentTests::*`（5 项，含"不改变租户 version"） | pass |

### Requirement: 部分失败可恢复且不误报成功

| Scenario | 测试 | 结果 |
| --- | --- | --- |
| 单个智能体失败返回部分结果 | `FailureCompensationTests::test_a_failing_agent_is_reported_without_discarding_the_others` | pass |
| 失败清理与续跑 | `FailureCompensationTests::test_post_clone_failure_rolls_back_the_roster_and_the_workspace`、`test_rerun_after_a_failure_completes_the_copy` | pass |
| 孤儿 roster 条目被收养 | `FailureCompensationTests::test_an_orphan_roster_entry_is_adopted_not_duplicated` | pass |
| 前端不误报成功且可重试 | `test_tenant_tabbed_editor_frontend.cjs::a partial copy keeps the tab dirty, retryable and never claims success` | pass |

### Requirement: 授权、近期密码与审计

| Scenario | 测试 | 结果 |
| --- | --- | --- |
| 非平台管理员被拒 | HTTP：`test_non_platform_admin_post_is_forbidden_and_writes_nothing`、`test_get_requires_a_platform_admin` | pass |
| 缺少有效近期密码 | HTTP：`test_post_without_a_valid_recent_password_writes_nothing` | pass |
| 成功后写入脱敏审计 | HTTP：`test_post_writes_one_redacted_summary_audit` | pass |
| 路由登记为 platform 且 GET/POST 齐全 | `test_http_policy.py::HttpPolicyTests::test_tenant_agents_read_and_copy_are_registered`、`test_tenant_agents_get_reaches_handler_through_real_app` | pass |

### Requirement: 复制后运行时可用

| Scenario | 测试 | 结果 |
| --- | --- | --- |
| 无需重启即可使用 | HTTP：`test_post_copies_the_selection_and_reloads_the_runtime`（断言 `_reload_agent_runtime` 被调用）；验收：`VisibleAndUsableAfterCopyTests::test_the_clone_is_loadable_after_the_runtime_reload`、`test_the_target_member_can_list_and_use_the_clones` | pass |
| 来源租户可见性不变 | 验收：`VisibleAndUsableAfterCopyTests::test_the_source_tenant_is_untouched`；隔离：`IsolationAcceptanceTests::test_another_tenant_cannot_reach_the_clone` | pass |
| 克隆工作区落在目标租户根内 | 验收：`IsolationAcceptanceTests::test_every_clone_workspace_is_inside_the_target_shared_root` | pass |

### Requirement: 兼容既有绑定与空态

| Scenario | 测试 | 结果 |
| --- | --- | --- |
| 既有绑定保持稳定 | `test_agent_clone_binding.py::AgentBindingMigrationUpgradeTests::test_upgrade_keeps_existing_bindings_with_a_null_provenance`、`test_replay_is_idempotent`、`AgentBindingCloneColumnTests::test_plain_bindings_are_not_constrained_by_the_partial_index` | pass |
| 非 database 模式不启用 | HTTP：`test_the_endpoint_is_closed_in_legacy_identity_mode` | pass |
| `create_agent(clone_from=...)` 行为不变 | `test_agent_clone_primitives.py::test_create_agent_clone_from_still_behaves_the_same` | pass |

## tenant-management

| Scenario | 测试 | 结果 |
| --- | --- | --- |
| 平台管理员管理租户列表（标签集合与顺序） | `test_tenant_tabbed_editor_frontend.cjs::the editor adds an agent tab between tool and admin`、既有 `editing a tenant opens the full-page tabbed editor, not the modal`（已更新为五标签） | pass |
| 按标签独立保存与草稿保留 | 既有 `switching tabs keeps uncommitted input and flags the draft unsaved`、`one save commits every dirty tab…`（已更新） | pass |
| 新建租户时标签可用性 | 既有 `create mode can edit the code and disables the other tabs`（已含 `agent`）；`create mode explains the agent tab and writes nothing` | pass |
| 展示租户智能体 | `the agent tab shows the tenant own agents read-only with a default marker` | pass |
| 展示可复制候选与已复制标记 | `the agent tab lists the source candidates and marks the already-copied ones` | pass |
| 空列表与读取失败可区分 | `a tenant with no agents says so, and a failed read is not reported as none` | pass |
| 未勾选即提交被阻止 | `submitting after unchecking everything sends no request and says what is missing` | pass |
| 勾选后复制要求近期密码 | `checking a candidate copies exactly the picked ids and echoes the result`、`the batch copies agents after grants and before basics, without touching the version` | pass |
| 复制失败不回显成功 | `a partial copy keeps the tab dirty, retryable and never claims success` | pass |
| 未创建租户时不提供写操作 | `create mode explains the agent tab and writes nothing` | pass |
| 来源不可解析时的说明态 | `an unresolvable source is explained without losing the tenant own list` | pass |
| 三语 i18n 键齐全（含补上的 `admin_back_to_list`） | `test_i18n_tenant_editor_keys.cjs`（4 项） | pass |

## 未覆盖 / 已知限制

- 目标租户根在只读或无权限时"所有智能体都失败"的路径未做端到端实测：单元层已由 `FailureCompensationTests` 覆盖失败明细与补偿，未在真实文件系统上模拟 EACCES。
- 菜单可见性不构成授权：本 change 未新增菜单项（复用既有租户菜单），越权路径由 HTTP 层 403 + 拒绝审计覆盖。
- 全仓 `pytest tests/` 存在与本次无关的既存失败（`test_claude_thinking`、`test_feishu_progress_card`、`test_read_edit_improvements`、`test_security_ssrf_browser_navigate`、`test_session_history_search`、`test_identity_self_context` 的 `avatar` 字段，以及若干仅在整仓顺序下出现的污染用例），均不在本 change 触及的模块内；本 change 相关模块在上述定向运行中 116 项全通过。
