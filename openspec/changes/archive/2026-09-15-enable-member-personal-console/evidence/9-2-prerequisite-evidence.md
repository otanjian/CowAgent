# Stage 9.2 — 前置切片证据复核

任务：逐阶段核对前置切片的实际证据；私有知识与在途知识权限变更完成合并后才开放对应写
路径，个人执行必须完成实际渠道验收。

`evidence/1-3-prerequisite-evidence.md` 自述是**时点快照**（会话期间
`restrict-knowledge-write-authorization` 从 0/25 变为 24/25）。本文件是在交付时点对同一批
门槛、以及本次新增的四个能力开关逐个复核的结果。核对方法：规范是否已合并 → 实现位置 →
真实消费者 → 用例 → **复制运行**。

## 1. 设计阶段门槛 ←→ 开关出箱姿态

| 设计门槛（design.md:130-137） | 要求的证据 | 实际证据 | 判定 | 开关姿态 |
| --- | --- | --- | --- | --- |
| 个人入口与目录 | 真实身份库、默认与自定义菜单矩阵、当前租户过滤、403/503 与迟到响应 | `evidence/8-personal-console-evidence.md`；`tests/test_personal_console_acceptance.py`、`test_personal_console_web.py`、`test_personal_console_transport.py`、前端 `test_personal_console_frontend.cjs`（56）、浏览器契约 `test_personal_console_browser.cjs` | **已具备** | `member_personal_console` = 开 |
| 私有智能体与记忆 | owner 动作矩阵、实际文件来源隔离、版本冲突/补偿、记忆检索一致性、运行依赖授权 | `evidence/3-6-private-maintenance-acceptance.md`、`evidence/5-5-personal-memory-evidence.md`；`tests/test_private_agent_owner_actions.py`（20）、`test_private_agent_web_gate_ordering.py`（11）、`test_private_agent_file_scope.py`（含预览消费期复检 6）、`test_private_resource_acceptance.py`（13）、`test_personal_memory_console.py`、`test_user_personal_memory.py` | **已具备** | `user_private_agent_management` = 开；`personal_memory_write` = 开 |
| 个人渠道接入 | 密文落点、身份挑战防重放、应用冲突检测、原子配额、审计失败与连接失败恢复 | `evidence/6-8-personal-channel-evidence.md`（含 8 项变异全捕获）；`tests/test_personal_channel_console.py`、`test_personal_channel_inbound.py`、`test_personal_channel_binding.py`、`test_personal_instance_policy.py` | **已具备** | `personal_channel_onboarding` = 开 |
| 个人渠道执行 | 真实支持渠道的本人私聊到实际分发链路、他人/群聊拒绝、解绑与停用生效、凭证/配额/**适用审批**及**隔离**切片 | `evidence/7-personal-channel-execution.md`：桥接与分析链路已验（M1-M8 变异全捕获），但**无任何渠道类型完成真机端到端验收**；`PERSONAL_RUNTIME_ACCEPTED_TYPES = frozenset()`、`PUBLIC_PERSONAL_INGRESS_TYPES = frozenset()`（`channel/channel_instances.py:1091`、`:1097`） | **未具备** | `personal_channel_runtime` = 关（`config.py` 默认 `False`） |

结论：三个门槛具备真实证据的能力按开出厂；执行类门槛缺真实验收，工厂态关闭 ——
这正是任务 7.5 的「关闭 + 准确记录待验收范围」分支，**不以打桩用例声明生产能力已开放**。

## 2. 四个登记门槛的复核

| 编号 | 1.3 的判定 | 交付时点复核 | 结论 |
| --- | --- | --- | --- |
| **Q1** 配额并发竞争 | 无独立测试 | 已有：`tests/test_stage2_migration_and_quota_evidence.py::test_a_user_bucket_race_also_holds`、`test_concurrent_larger_amounts_still_respect_the_limit`；`tests/test_personal_instance_policy.py::test_concurrent_creation_cannot_take_the_last_slot_twice`、`test_two_concurrent_requests_both_fit_under_a_limit_of_two`（后者即 2.5「双请求原子配额」）。本次复制运行全绿 | **已解除** |
| **Q2** `action-approval` 无真实消费者 | 仅服务层占位，登记关闭门槛 | `rg "approval"` 在 `auth/` 与 `tests/` 外仍**零命中**，故本 change **不开放任何需要审批的能力**；设计把「适用审批」列为个人渠道**执行**门槛的一部分，而执行已关闭 → 该切片不阻塞任何已开启开关 | **保持开放但未消费**：不影响出厂姿态，若将来要开个人执行，须先满足 |
| **Q3** 个人渠道语境的隔离验收 | 个人路由隔离证据待补 | `evidence/7-personal-channel-execution.md` §2 已覆盖入站路径验证（instance tenant/scope、发送者证明、有效成员、个人关联、target owner）与「无回退到公共/平台默认」；请求期与合成期各再判一次 | **已具备**（但仅在执行关闭的前提下有意义） |
| **Q4** 私有知识写依赖在途 change | 依赖 `restrict-knowledge-write-authorization`，1/25 时点**不得先行** | 该 change **已归档合并**：`openspec/changes/archive/2026-09-14-restrict-knowledge-write-authorization/` 全部任务勾选，其 6.3 记录了真实浏览器验收（成员对租户共享库可读、写入口隐藏且直接 POST 403；成员对自有 `knowledge_mode=own` 私有库写入成功；租户管理员两者均可写）。合并落地核对：`can_write_knowledge` / `knowledge_mode` 已有多处实现与用例（`auth/store.py`、`agent/admin.py`、`agent/personal_assistant.py`、`channel/web/web_channel.py`）；迁移 `_migration_15` 已存在且后续 16-19 顺延；权限目录不再含 `knowledge.write`（`tests/test_identity_policy.py`、`tests/test_builtin_role_editing.py` 全绿） | **已解除**，本 change 的私有知识写路径（任务 3.5）**允许开放** |

补充：本 change 在 3.5 已把 owner 优先检查与 `can_write_knowledge` 投影对齐（投影直接调用
`_knowledge_write_authorized`），因此「公共写资格」不会投影成「所有私有对象可写」；3.4 移除
`_db_path_owner_forbidden` 的 `tenant_admin` 旁路后，私有知识的**读**路径同样只对 owner 开放。

## 3. 复制运行的证据

```
.venv/bin/pytest tests/test_personal_console_acceptance.py tests/test_personal_console_web.py \
  tests/test_personal_console_transport.py tests/test_private_agent_owner_actions.py \
  tests/test_private_agent_web_gate_ordering.py tests/test_private_agent_file_scope.py \
  tests/test_private_resource_acceptance.py tests/test_personal_capability_switches.py -q
→ 150 passed

.venv/bin/pytest tests/test_personal_memory_console.py tests/test_user_personal_memory.py \
  tests/test_personal_memory_tool_execution.py tests/test_personal_channel_console.py \
  tests/test_personal_channel_inbound.py tests/test_personal_channel_binding.py \
  tests/test_personal_instance_policy.py tests/test_knowledge_console_database.py \
  tests/test_knowledge_web.py tests/test_private_agent_file_scope.py -q
→ 284 passed
```

## 4. 并发 change 的合并次序（供 9.5/9.6 使用）

- `openspec/changes/complete-database-capability-parity` 仍在途，与本 change **共用**
  `console-navigation-availability` 能力。逐条比对 requirement 名：本 change 的
  「显式菜单授权限制页面可见性」「个人能力开关只控制开放且不替代权限检查」与对方的
  「旧模式与未适配消费者维持边界」「开放依据真实依赖切片与一致版本」「恢复页面按本人维护
  和公共管理分别呈现」**无同名冲突**，可分别归档。
- 对方 change 的用例明确引用「个人控制台依赖尚未完成」——即它把本 change 当作前置，
  因此归档次序应为 **本 change 先行**，否则其数据库调度/记忆页可能早于个人控制台开放。
- 本 change 的 `console-navigation-availability` delta 尚未合并进主规范（`openspec/specs/`
  下该能力仍无本 change 的两条 requirement），归档时按既有 OPENSPEC 流程 apply delta。

**补充（2026-09-15）**：`complete-database-capability-parity` 已于 2026-09-15 **部分归档**
（归档名 `2026-09-15-complete-database-capability-parity`），其 `console-navigation-availability`
三条增量（`旧模式与未适配消费者维持边界`、`开放依据真实依赖切片与一致版本` 为 MODIFIED，
`恢复页面按本人维护和公共管理分别呈现` 为 ADDED）已 apply 进 `openspec/specs/console-navigation-availability/spec.md`，
与本 change 的两条 requirement 无同名冲突。上述归档次序问题已消解：本 change 的两条 requirement 仍需在
本 change 归档时按同一流程 apply——`显式菜单授权限制页面可见性`（MODIFIED，主规范已有基线文本待更新）、
`个人能力开关只控制开放且不替代权限检查`（ADDED，主规范尚无）；未验收的 Desktop 与微信执行增量已移交
`complete-desktop-and-scan-real-acceptance`，不影响本 change 的合并。
