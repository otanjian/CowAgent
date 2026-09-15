# 13. R1-R5 本轮审查补充验收门槛

- 记录日期：2026-09-15
- 规则：本节只记录**实际执行过的命令与真实结果**。未执行或只覆盖一部分的门槛明确写「未通过」，
  对应任务保持未勾选；不以规划产物或单侧测试代替。

| 门槛 | 结论 | 一句话原因 |
| --- | --- | --- |
| R1 定时工具闭环 | **通过** | 六个动作经真实 `ToolManager` 分发跑通，工具/HTTP/后台共用同一授权服务与写入协调 |
| R2 Desktop 原生协议闭环 | **未通过** | 协议级（本地）已验；远程 HTTPS 形态、兑换响应丢失重试与 8.7 真实客户端演练未执行 |
| R3 飞书兼容边界 | **通过** | 飞书发起/领取/创建的一次性契约、非发起者拒绝与秘密不回显均有真实用例 |
| R4 扫码一次性与幂等一致 | **通过** | 微信侧端到端 WSGI 链路（40 例经真实 `build_web_app()`）与飞书「二次创建仍拒」均已验；此前登记的 handler 崩溃与注释悬空已修复（见下） |
| R5 接缝回退与合并契约一致 | **通过（补强后）** | 补上必需接缝清单与启动检查；四种形态各有对应用例 |

---

## R1 定时工具闭环

**要求**：经真实 Agent 工具分发执行 create/list/get/delete/enable/disable；覆盖同一公共 Agent 下不同用户、
跨租户、非 owner 管理员、身份快照失败、配额耗尽、撤权后暂停/删除与跨入口并发；工具、HTTP 和后台共用
目标授权、审计及 TaskStore 写入协调。

**实现位置**

- 唯一授权服务：`agent/tools/scheduler/authorization.py`（`TaskAccessService`）——工具、HTTP handler
  （`channel/web/web_channel.py` 的五个 `Scheduler*Handler`）和启动迁移共用它，不存在第二份判定。
- 写入协调：`agent/tools/scheduler/task_store.py` 的 `expected_revision`（乐观并发）与 `TaskWriteLease`
  （多写者部署的进程级租约）。
- 执行侧重验：`agent/tools/scheduler/identity.py` 的 `revalidate_owner`（数据库身份下无主任务为
  `UNATTRIBUTED` 并跳过执行）。
- 审计与配额：`auth/service.py` 的 `record_business_audit`、`check_scheduled_task_quota`。

**逐条证据（`tests/test_scheduler_tool_dispatch.py`）**

| 门槛条目 | 用例 |
| --- | --- |
| 六个动作经真实工具分发闭环 | `test_all_six_actions_round_trip_for_the_owner` |
| 工具注册与分发路径真实 | `test_the_tool_manager_registers_the_scheduler_tool` |
| owner 由已验证身份盖章而非模型输入 | `test_creation_stamps_the_verified_owner_not_the_model_input` |
| 同一公共 Agent 下不同用户互不可见/不可操作 | `test_a_second_member_on_the_shared_agent_cannot_see_or_touch_the_task` |
| 非 owner 租户管理员 | `test_a_tenant_admin_cannot_reach_another_members_personal_task` |
| 无 `agent.use` 授权不得创建 | `test_a_member_without_agent_use_cannot_create` |
| 跨租户 | `test_a_cross_tenant_member_cannot_address_the_agent` |
| 身份快照/身份不可验证 | `test_an_unverifiable_identity_cannot_create` + `tests/test_scheduler_identity_revalidation.py`（含 `UNATTRIBUTED` 跳过执行） |
| 配额耗尽（创建与重新启用都计量） | `test_the_quota_refuses_creation_through_the_tool`、`test_enabling_a_disabled_task_is_metered_too` |
| 撤权后仍可暂停/删除本人任务 | `test_a_revoked_member_can_still_pause_and_delete_their_own_task` |
| 跨入口并发（工具 ↔ Web 同一授权与同一存储） | `test_a_task_created_by_the_tool_is_governed_by_the_web_console`、`test_a_task_created_in_the_console_is_governed_by_the_tool` |

**命令与真实结果**

```
.venv/bin/python -m pytest tests/test_scheduler_tool_dispatch.py tests/test_scheduler_task_authorization.py \
    tests/test_scheduler_task_migration.py tests/test_scheduler_web_update.py \
    tests/test_scheduler_identity_revalidation.py tests/test_scheduler_silent.py -q -p no:randomly
→ 79 passed
```

**授权开放**：`auth/capability_matrix.py` 的 `scheduler` 切片 `implemented=True, accepted=True`，
`open={list:read, toggle:config, update:config, delete:config, run:execute}`，策略 `tenant`。
`run` 属执行类但已在 R1 内验：执行只入队既有调度器，且执行前仍按 owner 重验。

---

## R2 Desktop 原生协议闭环 —— **未通过**

**已验（协议级，本地 loopback 形态）**

```
.venv/bin/python -m pytest tests/test_desktop_auth_flow.py -q      → 35 passed
node --test tests/test_desktop_context_frontend.cjs                → 14 passed / 0 failed
```

覆盖：系统浏览器确认页（CSRF/origin/活跃会话三重校验）、PKCE S256（明文被拒）、60 秒单次授权码、
错误 client/redirect_uri/verifier 拒绝、授权码重放拒绝、发起 Web 会话被撤销后兑换失败、
在线撤销已签发会话、`/auth/login` 恒空 token（自报 Desktop 仍只获 Cookie）、
主进程 Bearer 仅内存、通用 httpRelay 无法抵达后端/认证路径/携带凭据、
preload 窄化通道、epoch 作废迟到响应、无头传输走 broker 铸造的 loopback 资源代理、
渲染进程不持久化令牌且 URL 不含令牌。

**未验（因此本条不通过）**

1. **远程 HTTPS 后端形态**：现有用例全部经本地 `web_app`（loopback）。远程形态的
   回调/来源约束未单独演练。
2. **兑换响应丢失后的客户端重试**：授权码单次使用，响应丢失即需重新授权；客户端此时的行为
   （进入可操作的重试态而非空白页）没有对应用例。
3. **旧后端拒绝降级**：仅有「自报 Desktop 仍只获空 token」的服务端断言，
   旧后端 + 新客户端的拒绝降级未演练。
4. **8.7 真实打包客户端演练**：两个租户 × 两个成员 × 失效身份 × 重连未执行
   （约束禁止浏览器/Electron 自动化与 `npm install`）。
5. 上传/流/预览/下载的 **broker 路径**只在主进程/渲染进程模块级用例中验证，
   未经真实客户端端到端确认。

**投影一致性**：`desktop_tenant_context` 切片 `open={}, accepted=False, reason="awaiting_acceptance"`
（实测 consumer `available=False`），因此投影与「未验收」一致，不宣称可用。

---

## R3 飞书兼容边界

**要求**：确认「不回传长期密钥」只适用于微信新流程；飞书既有一次性契约、非发起者拒绝、
秘密不持久化/不显示保持不变。

**证据**：`tests/test_feishu_register_session.py`（14 例，本 change 未改动其断言）

- 只有发起者可读会话：`test_only_the_initiator_can_read_the_session`、
  未知句柄与外部句柄不可区分：`test_an_unknown_handle_is_indistinguishable_from_a_foreign_one`；
- 凭据只返回一次随后遗忘：`test_credentials_are_returned_once_and_then_forgotten`；
- 终态与轮询期都不带凭据：`test_terminal_states_carry_no_credentials`、
  `test_pending_session_reports_progress_without_credentials`；
- 轮询外部会话不夹带秘密、日志不出现秘密：`test_foreign_poll_never_carries_the_secret_in_its_body`、
  `test_no_register_log_line_carries_the_secret`；
- 表单契约仍提供飞书且不提供 `wechatcom_app`：`tests/test_tenant_channel_http.py::test_the_form_contract_offers_feishu_and_withholds_wechatcom_app`。

**边界声明**：本 change 的「长期密钥不回传」实现位于 `channel/web/scan_onboarding.py`
（微信扫码流的会话/回执/提交管道），飞书注册 handler 的返回字段未被改写；
把飞书切换到服务端托管凭据是另一个 change，不在本轮。

```
.venv/bin/python -m pytest tests/test_feishu_register_session.py tests/test_scan_authorization.py \
    tests/test_scan_onboarding_state.py tests/test_tenant_channel_http.py \
    tests/test_tenant_channel_instances_service.py tests/test_tenant_channel_credential_landing.py -q -p no:randomly
→ 150 passed
```

---

## R4 扫码一次性与幂等一致 —— **通过**

**已验**

| 条目 | 用例 |
| --- | --- |
| 同键同内容回读原结果 | `test_scan_onboarding_state.py::test_concurrent_submits_of_one_operation_commit_exactly_once`、`test_a_failed_receipt_store_is_resumable_without_a_second_instance`、`test_the_staged_tail_resumes_when_the_original_grant_is_still_live` |
| 新键/改内容重放拒绝 | `test_changed_content_or_binding_cannot_reuse_a_spent_authorization` |
| 跨用户/会话/作用域/provider 拒绝 | `test_cross_actor_reads_are_refused_without_disclosing_anything`、`test_commit_refuses_a_foreign_session`、`test_personal_scope_uses_the_member_entry_point_binding`、`test_a_grant_for_another_channel_type_is_refused_and_stays_usable` |
| 回读前撤权 | `test_readback_is_refused_when_the_binding_was_revoked`、`test_a_raising_readback_guard_refuses_the_replay` |
| 事务失败不消费 | `test_a_failed_audit_is_resumable_and_consumes_nothing`、`test_a_refused_write_keeps_the_authorization_and_the_retry_succeeds`、`test_a_quota_refusal_leaves_nothing_behind` |
| 24 小时回执与授权分别过期 | `test_a_receipt_outlives_the_session_that_started_it`、`test_an_expired_grant_does_not_take_the_receipt_with_it`、`test_an_expired_receipt_cannot_recreate_and_revives_nothing` |
| 回执清理不复活授权 | `test_purging_receipts_neither_deletes_nor_revives_grants` |
| 回执无秘密、无重复连接 | `test_no_secret_reaches_the_result_the_receipt_or_the_logs`、`test_a_projection_carrying_a_credential_value_is_refused_presumably`、`test_a_secret_in_a_request_field_is_refused_before_the_write` |
| 飞书未采用幂等协议时二次创建仍拒 | `tests/test_tenant_channel_http.py::test_a_scan_grant_is_redeemed_exactly_once`、`test_a_create_refused_for_another_reason_keeps_the_grant`、`test_a_create_with_neither_password_nor_grant_is_refused` |

**本轮已补齐的两项（原「未通过的原因」）**

1. **微信侧端到端 WSGI 用例已补齐**：`tests/test_weixin_qr_flow.py`（40 例，经真实 `build_web_app()`
   走完 `GET /api/weixin/qrlogin → POST（轮询/提交）→ 实例落库`）已全绿；`auth/capability_matrix.py`
   的 `weixin_scan` 切片注释所引用的正是该文件，注释与树重新一致。原先的 32 例失败根因不是断言口径，
   而是 handler 在进入状态机**之前**崩溃（`_auth_session_id` 对 `sqlite3.Row` 调 `.get()`，
   见 `evidence/9-route-and-ui-availability.md` 第 6 节阻塞 1）——已修复，异常不再被吞成 `400 scan_failed`。
   `tests/test_scan_onboarding_state.py`（37 例）继续从模块接缝侧覆盖并发/回执/事务失败，两者互补：

```
.venv/bin/python -m pytest tests/test_weixin_qr_flow.py -q -p no:randomly           → 40 passed
.venv/bin/python -m pytest tests/test_scan_onboarding_state.py -q -p no:randomly    → 37 passed
.venv/bin/python -m pytest tests/test_feishu_register_session.py tests/test_scan_authorization.py \
    tests/test_scan_onboarding_state.py tests/test_weixin_qr_flow.py tests/test_tenant_channel_http.py \
    tests/test_tenant_channel_instances_service.py tests/test_tenant_channel_credential_landing.py \
    tests/test_personal_channel_console.py tests/test_personal_channel_inbound.py \
    tests/test_personal_delivery_drill.py tests/test_personal_capability_switches.py -q -p no:randomly
→ 321 passed in 155.88s (0:02:35)
```

2. **7.9（适用动作审批消费者）已交付**：`agent/approval_gate.py` + 两处真实分发接缝
   （`agent/protocol/agent_stream.py` 的工具分发、`agent/tools/scheduler/integration.py` 的定时投递）
   + `tests/test_action_approval_consumer.py`（26 例，含待批无副作用、批准后同动作执行、
   拒绝/撤回/过期/换参/跨用户/跨租户/重放拒绝、单次消费与原子并发）。7.10 的类型开放仍以 7.8 为前提，
   `PERSONAL_RUNTIME_ACCEPTED_TYPES` / `PUBLIC_PERSONAL_INGRESS_TYPES` 保持空集。

**仍未验（不因 R4 通过而消失）**

- **真实 provider 扫码、真实连接、真实收发**（任务 7.8）：执行类由 `personal_runtime_enabled()`（默认关）
  与 `PERSONAL_RUNTIME_ACCEPTED_TYPES`（空）关闭，切片只开放 `qr`/`poll` 两个 config 动作；
  7.8 保持未勾选，7.10 因此保持未勾选。切片只声明 config 类验收，不声明 execution。

---

## R5 接缝回退与合并契约一致 —— **通过（补强后）**

**要求**：主规范「定制逻辑位于稳定接缝而非上游核心文件内」以完整 MODIFIED requirement 保留原场景
并明确回退边界；四种形态各有验收；必需接缝由独立启动装配检查，不能以开关降级鉴权；
scheduler tool/store、Desktop main/preload/broker 及认证响应纳入语义漂移与双侧回归清单。

**规范**：`openspec/changes/complete-database-capability-parity/specs/fork-upstream-decoupling/spec.md`
含 `## MODIFIED Requirements`（原文保留 + 回退边界：独立上游保持上游语义、rdai 仅可选展示接缝可回退、
强制授权接缝缺失须拒绝启动或关闭能力、请求参数/插件开关不得把 rdai 降格为独立上游）
与两个 ADDED requirement（恢复接缝保留上游功能与数据库强制边界；master→rdai 语义漂移检查范围）。

**本轮补强**：把「必需接缝由独立启动装配检查」从文字变成机制。

- `common/startup_hooks.py` 新增 `REQUIRED_HOOKS` 清单（身份模式一致性、首启 bootstrap、
  会话租户归属回填、定时任务归属迁移）与 `missing_required_hooks()` / `verify_required_seams()`。
  清单**与注册表同处声明**，而不是写在被检查的扩展里：删掉一行 `register_startup_hook` 不再
  静默退化为 no-op。
- `app.py` 新增具名入口 `_verify_required_seams()`，在 `run()` 里于身份模式守卫**之前**调用；
  它**直接**调用清单校验（不经 `run_startup_hook`），因此「未注册的接缝」无法跳过检查它的那段代码。

**四种形态的用例**

| 形态 | 用例 |
| --- | --- |
| 独立上游（无 fork 模块/无注册） | `test_startup_hook_seam.py::test_an_unregistered_hook_is_a_no_op`、`test_upstream_core_seams.py::test_app_py_has_no_inlined_fork_guard` |
| 完整 rdai | `test_startup_hook_seam.py::RequiredSeamManifestTests::test_every_required_seam_is_declared_and_armed`、`test_the_fork_registers_boot_seams` |
| rdai 强制授权接缝缺失 | `test_a_dropped_registration_refuses_the_boot`（`RuntimeError`，消息含缺失接缝名）、`test_the_boot_calls_the_check_before_any_guard_runs`、`test_the_check_is_not_itself_a_hook`、`test_the_boot_sequence_verifies_seams_before_the_identity_guard`、`test_upstream_core_seams.py::test_explicit_legacy_identity_mode_refuses_to_boot` |
| 仅可选 UI 接缝缺失（只回退展示） | `tests/test_fork_fragments.cjs::a missing optional UI seam falls back to display only and never touches authorization`（404 时挂载点保持为空、不触发 i18n/挂载事件、只请求声明的片段 URL、不触达 `/api/`、不写凭据或存储） |

**语义漂移与双侧回归清单**：`tests/test_upstream_drift_guards.py`（新增 HTTP 方法必须有策略、
恢复 URL 不得重复登记、handler 方法必须登记、真实登记表当前已分类；上游新增任务字段在编辑中保留、
且编辑仍不能伪造受保护身份字段）+ `tests/test_upstream_core_seams.py`（`_import_local_file` 的
loopback+每次启动令牌契约在合入后仍成立、不允许未鉴权的本地路径路由）。

```
.venv/bin/python -m pytest tests/test_startup_hook_seam.py tests/test_upstream_core_seams.py \
    tests/test_upstream_drift_guards.py -q -p no:randomly     → 39 passed, 1 skipped
node --test tests/test_fork_fragments.cjs                      → 6 passed / 0 failed
```

**边界（诚实声明）**：四种形态的验收是在**本仓树内**用接缝/装配/片段层面的用例完成，
不是一次真实的上游分支合并排练；「候选版本同时通过上游行为回归与 rdai 权限隔离回归」的
最终合并候选验证属于发布流程（见 `evidence/10-master-merge-preservation.md` 第 4 节记录的排练结论）。
