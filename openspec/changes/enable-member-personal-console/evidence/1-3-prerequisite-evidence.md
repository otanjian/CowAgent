# 1.3 前置切片证据核对（Stage 1）

本文件是 `enable-member-personal-console` 任务 1.3 的证据产物：逐项核对 design.md「阶段门槛」所依赖的
前置能力切片是否**有真实证据**（规范 + 实现 + 真实消费者 + 测试），并为证据不足者登记关闭门槛。

核对方法：`openspec/specs/*` 规范存在性 → 实现位置（file:line）→ **真实消费者是否接线** → 测试文件。

## 汇总

| 切片 | 规范 | 实现 | 真实消费者 | 测试 | 判定 |
| --- | --- | --- | --- | --- | --- |
| `credential-management` | ✅ 4 req | ✅ | ✅ 已接线 | ✅ 多文件 | **证据充分** |
| `audit-log` | ✅ 4 req | ✅ | ✅ 已接线 | ✅ | **证据充分** |
| `resource-quota` | ✅ 4 req | ✅ | ✅ 已接线 | ✅（共享文件） | **证据充分**（并发竞争证据待补，见下） |
| `execution-isolation` | ✅ 4 req | ✅ | ✅ 已接线 | ✅ | **证据充分** |
| `resource-execution-authorization` | ✅ 8 req | ✅ | ✅ 已接线 | ✅ 多文件 | **证据充分** |
| `action-approval` | ✅ 4 req | ✅ **仅服务层** | ❌ **无 auth/ 之外的消费者** | ⚠️ 仅 1 处 | **证据不足 → 登记关闭门槛** |
| `allow-personal-memory-tool-execution`（在途） | ✅ 已实现 | ✅ | ✅ 已接线 | ✅ 自有测试 | **证据充分**（3 项已知遗留，见 §B） |
| `restrict-knowledge-write-authorization`（在途） | ❌ 未实现 | ❌ | ❌ | ⚠️ 断言旧行为 | **证据不足 → Q4** |

## 逐项明细

### credential-management — 证据充分

- 存储：`credentials` / `credential_versions` 表（`auth/store.py`），密文版本化。
- 加解密：`auth/crypto.py` `encrypt_secret` / AES-256-GCM，主密钥来自 `COW_CREDENTIAL_MASTER_KEY`。
- 真实落地：渠道实例创建/轮换走同一事务（`auth/service.py:5330-5360` 轮换时同时写 `credential_versions` 与 `credentials.version`）。
- 测试：`tests/test_identity_credential.py`、`tests/test_tenant_channel_credential_landing.py`、
  `tests/test_tenant_channel_required_credentials.py`、`tests/test_weixin_credentials_path.py`。

### audit-log — 证据充分

- 存储：`audit_events` 表，且带**只增触发器**（`auth/store.py:172-191`，`BEFORE DELETE/UPDATE` → `RAISE(ABORT)`）。
- 写入：`_audit_in_tx(con, **kwargs)` → `self._audit.record(...)`（`auth/service.py:368`），与业务同事务提交。
- 真实落地：身份、渠道实例、配额拒绝等写路径均调用（如 `channel.instance.update`、`credential.rotate`、`quota.deny`）。
- 测试：`tests/test_identity_audit.py`。

### resource-quota — 证据充分（并发竞争证据待补）

- 存储：`quota_limits`（租户 + 可选 user 的硬上限）与 `quota_usage`（窗口用量），`auth/store.py:351-358`。
- 服务：`set_quota`（`:5873`）、`quota_status`（`:5904`）、`consume_quota`（`:5924`）；
  用量以原子 upsert 累加（`DO UPDATE SET used=quota_usage.used+excluded.used`），超限写 `quota.deny` 审计。
- **真实消费者**：`agent/protocol/agent_stream.py:2088` `allowed = svc.consume_quota(...)` —— 确实接在执行链路里。
- 测试：`tests/test_rbac_tenant_constraints.py`（含超限拒绝）、`tests/test_control_plane.py`。
- ⚠️ 无**独立**的配额并发竞争测试文件；本 change 任务 2.7 要求「双请求配额竞争测试」，
  2.5 要求「创建/启用采用原子配额判定」。→ 见下方门槛 Q1。

### execution-isolation — 证据充分

- 实现：`agent/permission/isolation.py`（工作区/state 目录边界，跨租户与身份库路径一律拒绝）。
- 测试：`tests/test_execution_isolation.py`，另有多租户隔离测试集
  （`test_conversation_tenant_isolation.py`、`test_tenancy_isolation_acceptance.py`、
  `test_tenant_channel_isolation_acceptance.py` 等）。
- 注意：规范明确「隔离未验收时默认拒绝任意代码」，该切片是个人渠道执行的硬前置。

### resource-execution-authorization — 证据充分

- 实现：`check_resource_action`（`auth/service.py:1253`）及执行闸门；窄豁免
  `tenant_admin_may_execute_tool`（`:1295`）、`personal_memory_tool_may_execute`（`:1345`）。
- 测试：`tests/test_execution_authorization_fail_closed.py`、`tests/test_rbac_execution_permission.py`、
  `tests/test_tenant_admin_tool_execution.py`、`tests/test_personal_memory_tool_execution.py`。

### action-approval — 证据不足 ⛔

- 实现（服务层齐全）：`approvals` 表（`auth/store.py:333`，含 `idx_approvals_tenant_status`）；
  `request_approval`（`auth/service.py:5664`）、`decide_approval`（`:5710`）、`cancel_approval`（`:5753`）、
  `revoke_approval`（`:5789`）、`list_approvals`（`:5826`），含职责分离与超时置 `expired`。
- **缺口**：`rg -rn "approval" --glob '*.py' -l .` 在 `auth/` 与 `tests/` 之外**无任何命中** ——
  即当前**没有任何高危外部副作用动作真正要求并消费审批**。该切片只是服务层占位。
- 测试：仅 `tests/test_control_plane.py` 覆盖服务本身，无端到端消费者验收。
- 规范要求「有副作用动作需审批」「审批资格与职责分离」—— 在缺少真实消费者前**不能按已验收能力依赖**。

## 附加：在途 change 的证据状态

### B. `allow-personal-memory-tool-execution` — 证据充分（19/22）

- 实现（未提交工作树）：`PERSONAL_MEMORY_TOOLS` 固定集与 `_memory_write_stays_own_scope`（`auth/service.py:130-159`）、
  `personal_memory_tool_may_execute`（`auth/service.py:1345-1389`）。
- 运行时接线：`agent/protocol/agent_stream.py:2133-2168`，豁免在 `check_resource_action` **失败之后**才尝试，
  且经 `getattr(...) + callable(...)` 失败即关闭；`arguments` 由 `:2052` 透传。
- 测试：`tests/test_personal_memory_tool_execution.py`（三组：Service/Gate/EndToEnd）、
  `tests/test_user_personal_memory.py`（含跨租户）。
- 遗留 3 项（其 tasks 第 5 节，均声明不在其范围内）：5.1 两个 `HEAD` 既有失败用例、5.2 `memory_get` 路径收窄、5.3 memory 工具未进入平台可分配目录。
- ✅ 其 delta **已合并**进 `openspec/specs/resource-execution-authorization/spec.md`（复核：主规范 `:84-124` 现含「本人作用域记忆工具豁免」段与 5 个记忆场景）。
- 其 change 目录已归档（`openspec/changes/archive/2026-09-14-allow-personal-memory-tool-execution/`）。
- ⚠️ 归档合并只覆盖执行授权；其中提到的「记忆管理面」仍由本 change 的 `user-personal-context` 承担，且实测**尚无**记忆 list/read/edit/delete/clear 管理接口与索引一致性实现（`/api/memory`、`/api/memory/content` 仍登记 `closed`）。

### A. `restrict-knowledge-write-authorization` — 证据不足（1/25）

- 仅 1.1 完成。**`can_write_knowledge`、`_knowledge_write_authorized`、`_migration_15` 全部不存在**（grep 零命中）。
- `knowledge.write` 仍在 `PERMISSION_CATALOG`（`auth/policy.py:53`）、`PERMISSION_METADATA`（`:171`）、
  `TENANT_ADMIN_DEFAULT_PERMISSIONS`（`:220`）。
- `clone_agent`（`agent/admin.py:611`）尚无 `knowledge_mode` 参数；`agent/personal_assistant.py:458` 未传该值。
- delta 未合并；且 `openspec/specs/tenant-knowledge-console/spec.md` 的 `Purpose` 仍是归档占位符 `TBD`。

## 登记的关闭门槛

| 编号 | 门槛 | 影响本 change 的 | 解除条件 |
| --- | --- | --- | --- |
| **Q1** | 配额并发竞争：无独立测试证明「双请求同时创建/启用」不会双花额度 | 2.5、2.7、6.8 | 在真实临时身份库上完成双请求原子配额竞争测试并留证 |
| **Q2** | `action-approval` 无真实消费者（仅服务层） | 7.5、6.8、9.2 中「适用审批切片」的前置 | 高危个人渠道/执行动作真正接入审批并有端到端验收 |
| **Q3** | `execution-isolation` 的**个人渠道语境**尚未验收（现有证据覆盖租户/工作区边界，未覆盖个人渠道入站与个人路由） | 7.2、7.4、7.5 | 个人路由在真实身份链路上通过隔离验收 |
| **Q4** | 私有知识写路径依赖在途 change `restrict-knowledge-write-authorization`（**已由 1/25 推进到 24/25 并落地代码**） | 3.5、9.2 | 该 change 合并且授权矩阵通过后，才开放私有知识写 |

> ⚠️ **本文件是时点快照**：会话期间 `restrict-knowledge-write-authorization` 从零实现变为 24/25 落地，
> 其 `_migration_15` 已占用；本 change 迁移号相应改为 `_migration_16`（见 1.1 的 C1）。
> 引用本文件的结论前须重新核对工作树。

> 结论：**凭据、审计、配额、资源执行、隔离**五类切片均有真实证据，可作为前置；
> **审批**切片仅服务层就位，且配额并发与个人渠道隔离证据待补 → 相关消费者按 Q1/Q2/Q3 保持关闭。
> **Q4** 的依赖方 `restrict-knowledge-write-authorization` 尚未开始实现，本 change 的私有知识写分支不得先行。

## 迁移版本号（供 Stage 2 使用）

`auth/store.py` 的 `_migrations` 注册表**当前到 `_migration_14`**（`:810`），无版本表上限常量，
下一个可用版本号是 **`_migration_15`**。

⚠️ **版本号冲突**：在途 change `restrict-knowledge-write-authorization` 的任务 3.2 **已明确声明**
`_migration_15`（剥离各角色的 `knowledge.write`）。两者不能同时占用 15 → 见 1.1 的合并结论。
