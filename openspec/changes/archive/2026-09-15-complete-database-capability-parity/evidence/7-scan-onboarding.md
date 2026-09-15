# 7. 微信扫码实例适配（任务 7.1-7.10）

- 记录日期：2026-09-15
- 结论：**7.1-7.7 已交付并通过验收**（R3、R4 通过，见 `13-review-supplementary-gates.md`）；
  **7.9 已交付**（适用动作审批消费者接入真实分发接缝，26 例通过）；
  **7.8 未通过**（真实微信提供方扫码/连接/收发需要外部条件，本环境不产生该证据），
  因此 **7.10 保持未勾选**：`PERSONAL_RUNTIME_ACCEPTED_TYPES` 与 `PUBLIC_PERSONAL_INGRESS_TYPES`
  仍为空集，承诺类型的个人执行与公共个人入口按设计保持关闭。
- 本文件只记**实际执行过的命令与真实结果**；未执行的部分在第 5 节明确列出。

## 1. 分工与实现位置（任务 7.1-7.4）

| 层 | 文件 | 职责 |
| --- | --- | --- |
| QR 会话与回执状态机 | `channel/web/scan_onboarding.py` | 绑定发起者 `AuthSession`/scope/tenant/owner/provider/目标的一次性会话；状态迁移表；24 小时幂等回执；授权消费 + 实例/凭据写入 + 配额 + 审计的原子提交 |
| 微信适配 | `channel/weixin_scan_adapter.py` | 真实 vendor 端点与登录句柄（`vendor_endpoint` 不由请求决定，见 `test_a_commit_cannot_move_the_vendor_endpoint`） |
| 实例与连接状态 | `channel/channel_instances.py` | 按显式实例读取密文、登记能力与连接状态；`personal_runtime_enabled` / `public_personal_ingress_ready` 的**逐类型验收 + 部署总开关**双重判定 |
| HTTP handler | `channel/web/web_channel.py`（`WeixinQrHandler`） | 薄处理器：门放行后把请求交给状态机；**不再持有进程级 `_qr_state`** |
| 登记与路由 | `auth/capability_matrix.py`（`weixin_scan`）、`channel/web/route_registry.py` | `qr`/`poll` 两个 **config** 动作开放，策略 `tenant`；execution 类不声明 |

### 7.1 状态机替换全局槽位

- `SESSION_TTL_SECONDS = 900`、`RECEIPT_TTL_SECONDS = 86400`；终态集合与允许迁移表显式声明
  （`_ALLOWED_TRANSITIONS`），终态不可复活。
- 两个发起者各持各自句柄与二维码：`test_two_initiators_hold_two_handles_and_two_qrs`；
  进程级槽位已不存在：`test_no_process_global_qr_slot_remains`。
- 未知句柄、他人句柄、已提交句柄对**任何**非持有者都是同一个不可区分拒绝：
  `test_a_foreign_missing_or_other_login_handle_is_one_identical_refusal`、
  `test_a_committed_handle_is_refused_the_same_way_by_everyone_else`。
- 多 worker 部署拒绝提供扫码（进程内共享状态不可用时不假装可用）：
  `test_a_multi_worker_deployment_refuses_to_serve_a_scan`；`shared_state_ready()` 是唯一判据。

### 7.2 一次性授权、原子提交与幂等回执

- 一次提交事务内消费授权、写实例/凭据、占配额、写审计、落幂等回执；任一步失败可**恢复重试**且不消费授权：
  `test_a_write_refused_later_keeps_the_authorization_for_a_retry`、
  `test_an_audit_refusal_resumes_the_same_scan_instead_of_rescanning`、
  `test_a_failure_after_the_instance_exists_requires_re_authorization`。
- 幂等绑定 actor/AuthSession/tenant/scope/owner/provider/授权句柄/目标/规范化摘要：
  `test_a_retried_submit_reads_the_receipt_back_instead_of_creating_again`；
  并发重试只提交一次：`test_concurrent_retries_of_one_scan_commit_exactly_once`、
  `tests/test_scan_onboarding_state.py::test_concurrent_submits_of_one_operation_commit_exactly_once`。
- 新键或变更内容不能重放已消费授权：`test_the_receipt_ledger_refuses_a_changed_content_replay`、
  `test_scan_onboarding_state.py::test_changed_content_or_binding_cannot_reuse_a_spent_authorization`。
- 微信兼容字段不回显长期密钥、不回写全局配置、不落日志：
  `test_the_stored_bundle_carries_only_the_declared_credentials`、
  `test_no_global_configuration_or_shared_file_is_written`、
  `test_scan_onboarding_state.py::test_no_secret_reaches_the_result_the_receipt_or_the_logs`。

### 7.3 显式实例、能力与连接状态

- 每个实例按 id 读取自己的密文；同一 bot 不能被两个个人实例占用：
  `test_the_same_bot_cannot_be_onboarded_twice_as_a_personal_instance`；
  一个实例不会启动第二条连接：`test_one_instance_never_starts_a_second_connection`。
- 连接状态与已保存行分开投影（`saved` / `connected` / `connection` / `connection_reason`）：
  `test_connection_state_is_reported_apart_from_the_saved_row`、
  `test_a_personal_scan_reports_saved_and_not_connected`。
- 类型/默认名称行为保留、全局配置不被覆盖：`test_no_global_configuration_or_shared_file_is_written`、
  `tests/test_tenant_channel_instances_service.py`、`tests/test_tenant_channel_credential_landing.py`。

### 7.4 个人实例落到同一绑定/配额/治理服务

- 个人扫码走同一实例服务（不复制 schema、不自绑 subject）：
  `test_a_member_scan_creates_their_own_instance_and_not_the_tenants`、
  `tests/test_scan_onboarding_state.py::test_personal_scope_uses_the_member_entry_point_binding`。
- scope 由服务端决定而非客户端声明：`test_scope_is_decided_by_the_server_not_the_client`。
- 平台管理员看不到别的租户的会话：`test_a_platform_admin_of_another_tenant_cannot_reach_the_session`。

### 7.5 已验证 owner 的本人私聊路由（组件级）

- 复用已交付的 `resolve_personal_channel_inbound` + 绑定挑战（挑战码必须由发送者本人账号发出），
  未绑定、他人、跨租户、群聊都进不了私有 Agent：
  `test_only_the_bound_owner_reaches_a_private_agent_on_a_scanned_bot`、
  `test_a_cross_tenant_sender_cannot_reach_another_tenants_private_bot`。
- 这是**组件级**证据，**不代替** 7.8 的真实执行验收；用例内临时抬高两个类型集合，交付集合保持为空
  （`test_the_shipped_deployment_keeps_personal_execution_closed` 是这条边界的回执）。

## 2. 验收（任务 7.6、7.7）

| 7.6 条目 | 用例 |
| --- | --- |
| 多发起者/多租户并发 | `test_two_initiators_hold_two_handles_and_two_qrs`、`test_a_cross_tenant_sender_cannot_reach_another_tenants_private_bot` |
| 非 owner 平台管理员查询 | `test_a_platform_admin_of_another_tenant_cannot_reach_the_session` |
| 失效会话 / 状态过期 | `test_an_expired_session_is_refused_and_does_not_revive`、`test_scan_onboarding_state.py::test_an_expired_receipt_cannot_recreate_and_revives_nothing` |
| 来源校验 | `test_a_side_effecting_post_must_present_the_console_origin`、`test_the_scan_start_is_an_origin_checked_write_too` |
| 配额/审计/连接失败 | `test_scan_onboarding_state.py::test_a_quota_refusal_leaves_nothing_behind`、`test_a_failed_audit_is_resumable_and_consumes_nothing`、`test_a_failed_receipt_store_is_resumable_without_a_second_instance` |
| worker 状态一致性 | `test_a_multi_worker_deployment_refuses_to_serve_a_scan` |
| 同键同摘要只提交一次 | `test_concurrent_retries_of_one_scan_commit_exactly_once` |
| 响应丢失回读 | `test_a_retried_submit_reads_the_receipt_back_instead_of_creating_again` |
| 不同键/参数/会话重放 | `test_the_receipt_ledger_refuses_a_changed_content_replay`、`test_a_commit_must_present_a_scan_that_the_provider_confirmed` |
| 回读前撤权 | `test_scan_onboarding_state.py::test_readback_is_refused_when_the_binding_was_revoked`、`test_a_raising_readback_guard_refuses_the_replay` |
| 授权与回执分别过期 | `test_scan_onboarding_state.py::test_a_receipt_outlives_the_session_that_started_it`、`test_an_expired_grant_does_not_take_the_receipt_with_it` |
| 事务失败未消费 | `test_a_write_refused_later_keeps_the_authorization_for_a_retry` |
| 无重复连接 | `test_one_instance_never_starts_a_second_connection` |
| 飞书一次性契约回归 | `tests/test_feishu_register_session.py`（14 例）、`test_the_feishu_one_time_handoff_keeps_its_surface` |

7.7 的开放范围：`weixin_scan` 切片只声明 `qr`/`poll` 两个 **config** 动作，不声明 execution；
`test_the_scan_slice_is_open_for_exactly_what_was_proved` 与
`test_the_route_table_reads_the_registry_instead_of_a_second_policy` 锁定「只开放已被证明的部分」，
`test_the_open_route_policy_admits_the_console_and_refuses_outsiders` 锁定闸门行为。

## 3. 适用动作审批消费者（任务 7.9）

- 策略与消费：`agent/approval_gate.py`（`required_actions` / `request_digest` / `approval_decision`），
  授权服务侧 `auth/service.py::request_approval`（绑定 target+digest）与
  `consume_action_approval`（单次、原子、越权/换参/越目标/他人审批全拒）。
- 真实接缝两处：`agent/protocol/agent_stream.py::_approval_tool_denial`（Agent 工具分发，
  Bridge 创建的 Agent 走同一条路）与 `agent/tools/scheduler/integration.py::_execute_send_message`
  （定时外发消息，投递前判定，拒绝即不投递）。两处都在身份、隔离、资源授权、配额**之后**判定，
  参数中的审批引用在调用工具前被剥离，传输不会变成工具参数。
- 绑定内容：执行时从**当前运行身份**取 tenant/owner/Agent（不接受调用方声明），审批行本身还校验
  请求人、租户、动作与 `agent_id`；目标位在定时投递上是 `"<channel_type>:<receiver>"`
  （任务 action 模型里没有实例 id 字段，渠道按类型解析实例，因此类型 + 目标 + 全量 action 摘要
  是该接缝能给的最强绑定），参数摘要覆盖整个 action（含内容），换参即不同摘要。工具分发上的目标位为空、
  绑定靠动作 id + 参数摘要 + 身份。
- 非适用动作的服务端依据：`approval_gate.required_actions` 只承认部署显式声明的动作；
  未声明的动作有明确的"非适用"依据（`action_basis`，含 `NOT_APPLICABLE_ACTIONS` 里逐条记录的理由），
  不是"客户端没提就算过"。未接通消费方（身份或服务不可解析）时按 `approval_unverified` 拒绝，
  即规范里"未接通审批消费方时需审批动作 SHALL 保持拒绝"。

## 4. `weixin_scan` 为何在 7.8 未验时仍 `accepted=True`（登记口径）

这是本轮被明确提出过的疑问，答案写在登记里而不是靠读者推断：

- `accepted` 只覆盖切片**实际声明的**动作类，由 `auth/capability_matrix.py` 的模块 docstring 明确定义。
  本切片声明 `open={"qr": config, "poll": config}`，因此 `accepted=True` 断言的是**配置面**
  （会话绑定、一次性授权、幂等回执、原子提交、handler 往返）已被真实验收，见第 1-2 节；
  它对执行类没有任何声明，也没有任何声明会被读者理解成"执行已验收"。
- 另一半是**机械**约束而非声明：`_EXECUTE_REQUIRES_ACCEPTANCE` 使 `check_consistency()`
  拒绝"声明了 execute 动作但切片未 accepted"的登记，所以"accepted 却未验证执行"无法被误写出来。
  实测 `capability_matrix.check_consistency() == []`。
- 执行面的真实状态仍由运行期开关表达，而不是由 `accepted` 表达：`personal_runtime_enabled("weixin")`
  默认 False、`PERSONAL_RUNTIME_ACCEPTED_TYPES` 为空，`test_the_shipped_deployment_keeps_personal_execution_closed`
  钉住这一点；7.8/7.10 保持未勾选。

## 5. 命令与真实结果

```
.venv/bin/python -m pytest tests/test_scan_onboarding_state.py -q -p no:randomly
→ 37 passed in 0.10s
.venv/bin/python -m pytest tests/test_weixin_qr_flow.py -q -p no:randomly
→ 40 passed in 17.60s
.venv/bin/python -m pytest tests/test_scan_authorization.py -q -p no:randomly
→ 10 passed in 0.05s
.venv/bin/python -m pytest tests/test_feishu_register_session.py -q -p no:randomly
→ 14 passed in 0.08s
.venv/bin/python -m pytest tests/test_action_approval_consumer.py -q -p no:randomly
→ 26 passed in 4.78s

.venv/bin/python -m pytest tests/test_feishu_register_session.py tests/test_scan_authorization.py \
    tests/test_scan_onboarding_state.py tests/test_weixin_qr_flow.py tests/test_tenant_channel_http.py \
    tests/test_tenant_channel_instances_service.py tests/test_tenant_channel_credential_landing.py \
    tests/test_personal_channel_console.py tests/test_personal_channel_inbound.py \
    tests/test_personal_delivery_drill.py tests/test_personal_capability_switches.py -q -p no:randomly
→ 321 passed in 155.88s (0:02:35)
```

## 6. 未通过 / 未覆盖（明确列出，不臆测）

1. **7.8 真实提供方验收未执行**：真实手机扫码、真实云端连接、真实消息收发需要提供方账号与手机端，
   本环境不产生该证据；公共与个人 scope 都未验收。**7.8 保持未勾选**，`PERSONAL_RUNTIME_ACCEPTED_TYPES`
   与 `PUBLIC_PERSONAL_INGRESS_TYPES` 保持空集（`test_the_shipped_deployment_keeps_personal_execution_closed`
   会在有人无验收就打开时失败）。
2. **7.10 因此未勾选**：类型记录未更新（无从更新——没有已验收类型），个人执行与公共个人入口按设计投影为关闭；
   "配置已保存但执行关闭"这一半已被
   `test_a_personal_scan_reports_saved_and_not_connected` + `test_the_shipped_deployment_keeps_personal_execution_closed`
   断言，但任务的开放前置未满足，故不勾选、不扩大范围。
3. **真实队列/时序**：`test_concurrent_retries_of_one_scan_commit_exactly_once` 在**同一进程内**用线程制造并发；
   真正的跨进程并发提交（多 worker）在**拒绝服务**这条路径上被覆盖，未在真实多进程部署上演练。
4. **审批接缝的部署策略未在任何真实部署上开启**：`approval_required_actions` 出厂为空，
   消费方已交付并有真实接缝用例；"某个具体部署声明了哪些动作"属于部署决策，不在本 change 断言范围。
