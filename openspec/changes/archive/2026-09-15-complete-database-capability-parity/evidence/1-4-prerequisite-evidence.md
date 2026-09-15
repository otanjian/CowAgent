# 1.4 前置证据：owner、凭据、审计、硬配额、执行隔离与适用审批

- 记录日期：2026-09-15
- 方法：先看实现位置与真实消费者，再看用例；缺少证据的动作/执行切片保持关闭。
- 结论概要：owner、按实例密文凭据、脱敏审计、硬配额与执行隔离在实施起点**已具备可复用
  实现**；记忆路径与发布并发（5.6-5.8）、所承诺的个人执行（7.8-7.10）与适用审批消费方
  由本 change 承接，未完成前对应动作保持关闭。

## 1. 归属（owner）

| 面 | 实现位置 | 真实消费者 | 证据 |
| --- | --- | --- | --- |
| 调度任务 owner/scope | `agent/tools/scheduler/authorization.py`（`TaskActor`、`actor_from_identity_context`、`actor_from_runtime`、`task_owner`/`task_scope`） | HTTP 五个 handler、`SchedulerTool`、后台循环 | `tests/test_scheduler_tool_dispatch.py`、`tests/test_scheduler_task_authorization.py` |
| 私有 Agent 归属 | `agent/private_agent.py`、迁移 22（`_migration_22` 的租户策略） | 个人控制台、私有 Agent 生命周期 | 兄弟 change `3-6-private-maintenance-acceptance.md`；本 change `tests/test_private_agent_owner_actions.py` |
| 个人渠道实例密文 owner | `channel/channel_instances.py`、迁移 21/23/24 | 个人渠道配置、共享实例个人路由 | 兄弟 change `6-8-personal-channel-evidence.md`、`7-personal-channel-execution.md` |

调度 owner 的判定不在工具内自证：`self_authorized=True` 只表示工具必须自行完成同等对象
授权，`SchedulerTool` 因此完全委托 `TaskAccessService`，不直接读写 `TaskStore`。

## 2. 凭据

| 面 | 实现位置 | 证据 |
| --- | --- | --- |
| 身份库密文（密码哈希、会话） | `auth/crypto.py`、`auth/store.py` | `tests/test_identity_service_writes.py` |
| 渠道实例密文凭据（含 owner 维度） | `channel/channel_instances.py` 的实例字段与密文存取 | `tests/test_tenant_channel_instances_service.py`、`tests/test_tenant_channel_credential_landing.py`（兄弟 change 范围内） |
| 一次性扫码授权 | `auth/scan_authorization.py`（`mint/verify/consume`，600s） | `tests/test_scan_authorization.py` |
| 本机导入一次性用途令牌 | 上游 `_import_local_file` 尚未落到本树（义务记于 `scripts/conflict-baseline.txt`）；本 change 在 `channel/web/project_import.py` 以同语义实现 loopback + 每启动令牌 + 一次性句柄 | `tests/test_scoped_project_browse.py`、`tests/test_platform_file_browsing.py` |

真实缺口：微信扫码的长期密钥在起点仍写全局 `conf()['weixin_token']`，且 `_qr_state` 是进程
全局单槽。该缺口由 7.1-7.3 承接，未修复前对应方法保持关闭（见 `evidence/7-*.md`）。

## 3. 审计（脱敏）

| 面 | 实现位置 | 证据 |
| --- | --- | --- |
| 身份/业务审计写入 | `auth/service.py`（`record_business_audit`、审计表与查询） | `tests/test_identity_service_writes.py` |
| 调度动作审计（成功与拒绝都记） | `agent/tools/scheduler/authorization.py::_audit` | `tests/test_scheduler_task_authorization.py::test_every_refusal_and_write_is_audited_without_task_content`（断言审计条目中不含任务正文/提示词） |
| 拒绝事件可观测 | `common/security_events.py::record_denial`（HTTP 网关拒绝） | `auth/http_policy.py` 的 `_record_gate_denial` |

审计与任务文件不在同一事务：`TaskAccessService` 先写任务再写审计，审计失败时返回
`audit_failed` 语义并可通过持久化操作记录补偿，不谎报“可重试的成功”。

## 4. 硬配额

| 面 | 实现位置 | 证据 |
| --- | --- | --- |
| 调度任务数量配额 | `auth/service.py::check_scheduled_task_quota`、`_QUOTA_METRICS` 中的 `scheduled_tasks` | `tests/test_scheduler_tool_dispatch.py`（创建与重新启用均计费，超限 `quota_exceeded`） |
| 私有 Agent 数量配额 | 兄弟 change 交付的私有 Agent 配额方法 | `tests/test_private_agent_quota.py` |
| 渠道实例配额 | 兄弟 change 交付的个人渠道配额 | 兄弟 change `6-8-personal-channel-evidence.md` |

配额在服务端判定：HTTP 与工具共用同一动作矩阵与配额检查，客户端参数不能绕过
（`tests/test_scheduler_task_authorization.py::test_the_quota_gate_applies_to_creation_and_to_reenabling`、`test_the_quota_refuses_creation_through_the_tool`）。

## 5. 执行隔离

| 面 | 实现位置 | 证据 |
| --- | --- | --- |
| 工具调用隔离与权限面 | `agent/permission/isolation.py`、`agent/tools/tool_manager.py` | `tests/test_execution_isolation.py`、`tests/test_execution_permission_ui.cjs` |
| 调度触发身份重验 | `agent/tools/scheduler/identity.py`（`revalidate_owner`、`database_identity_enforced`、`UNATTRIBUTED`） | `tests/test_scheduler_identity_revalidation.py` |
| 已撤权成员的执行 | `TaskAccessService.decide` 的 `run` 分支 + 触发前重验 | `tests/test_scheduler_tool_dispatch.py` 的撤权用例 |

## 6. 适用审批（本 change 承接）

- 起点事实：兄弟 change 的 9.2/Q2 与 9.6 记录**未交付**适用审批消费方；其 9-6 明确
  “缺少真实渠道条件时保持关闭”，不作为个人执行或适用审批通过的证明。
- 既有服务：`auth/service.py` 的 `request_approval / decide_approval / cancel_approval /
  revoke_approval / list_approvals / expire_approvals`（引擎可用）。
- 本 change 责任（7.9）：在承诺的实际 Bridge/工具分发接缝接入该服务，覆盖待批无副作用、
  批准后同一动作执行、拒绝/撤回/过期/参数改变/重放拒绝、执行前身份/资源/配额复验。
- 未接通前：相关动作只被拒绝，已交付配置与其他独立能力不退回。
- **交付结果（2026-09-15 更新）**：消费方**已交付**——`agent/approval_gate.py` 判定适用性并消费，
  接入 `agent/protocol/agent_stream.py`（工具分发）与 `agent/tools/scheduler/integration.py`
  （定时投递）两处真实接缝，`tests/test_action_approval_consumer.py` 26 例通过；
  部署开关 `approval_required_actions` 出厂为空。记录见 `evidence/7-scan-onboarding.md` 第 3 节。

## 7. 属于本 change 的修复前置（未完成即不得声明该切片开放）

| 前置 | 归属任务 | 开放影响 |
| --- | --- | --- |
| 记忆路径真实归属校验（枚举/元数据/读取/保存/删除/清空，含检查后替换） | 5.6 | 阻断 5.4（2 个记忆 GET 方法）与 5.5 |
| 正文/索引/清空版本与 generation 协调（含 save 释放锁后索引复活窗口） | 5.7 | 阻断 5.4、5.5 |
| 可控并发屏障验收（保存未发布索引时清空、旧固化与清空交错等） | 5.8 | 阻断 5.4、5.5 |
| 历史无 owner 任务隔离与迁移演练 | 4.1-4.3 | 已由本 change 完成（见 `evidence/4-*.md`） |
| 所承诺微信类型/scope 的真实扫码与收发、开放记录 | 7.8、7.10 | 阻断个人执行与公共实例个人入口；公共 QR 配置可在自身前置通过后先交付（**已交付**，见 `evidence/7-scan-onboarding.md`） |
| 适用审批消费方 | 7.9 | **已交付**（`agent/approval_gate.py` + 两处真实接缝 + 26 例）；未接通时需审批动作保持拒绝 |
