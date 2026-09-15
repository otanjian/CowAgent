# 1.1 兄弟 change 合并梳理（Stage 1）

本文件是 `enable-member-personal-console` 任务 1.1 的证据产物：梳理四个兄弟 change 的**实际改动**，
记录 requirement 归档顺序与接口交集，确保本 change 不覆盖其成果。

状态：**完成**。四份盘点已回填（§1–§7），冲突 C0–C3 已登记并给出处置。

> ⚠️ **时点快照提示**：本文件的核实工作在**同一工作树被并发修改**期间进行。
> 最明显的一次漂移：`restrict-knowledge-write-authorization` 从 1/25 零实现变为 24/25 落地，
> 并占用了 `_migration_15`（见 C1）。**引用本文件任何行号前须重新核对当前工作树。**

## 1. 兄弟 change 总览

| change | 任务进度 | 归档 | 本 change 的合并职责 | 状态 |
| --- | --- | --- | --- | --- |
| `fix-private-agent-owner-reachability` | ✓ 全部 | ✅ 已归档 | 复用统一 owner 读/用入口；本 change **扩展**写/启停，取代其「所有权不放宽写动作」旧限制 | ✅ 见 §6 |
| `fix-private-agent-file-scope` | 24/25（余 4.3 现场重启验证） | ✅ 已归档 | 复用实际文件来源校验；本 change **收紧**其管理员私有读取例外 | ✅ 见 §7 |
| `open-tenant-workspace-console` | ✓ 全部 | ✅ 已归档 | 复用工作区访问链路 | ✅ 见 §7 |
| `restrict-knowledge-write-authorization` | 1/25 | ❌ 仍在途 | 保留其公共知识写与目录迁移；本 change 对私有数据**优先 owner 检查** | ✅ 见 §3 |
| `allow-personal-memory-tool-execution` | 19/22 | ✅ 已归档 | 保留其记忆工具豁免；本 change 在其上做记忆管理面 | ✅ 见 §4 |

> 归档时点：三个兄弟 change 在本会话期间被归档，delta 随之并入主规范 → 触发冲突 C0。

## 2. 已确认的跨 change 冲突

### 冲突 C0（状态变更，已处理）：兄弟 change 批量归档，delta 基线移动

本 change 的 spec delta 是在兄弟 change **合并前**的基线上写的。会话期间三个兄弟 change 被归档
（`fix-private-agent-owner-reachability`、`fix-private-agent-file-scope`、`open-tenant-workspace-console`，
均在 `openspec/changes/archive/2026-09-14-*`），其新增 requirement 文本与场景并入主规范。

后果：`openspec validate --strict` 新报 **4 个错误、14 个场景被隐式丢弃**（MODIFIED 块替换整段 requirement）：

| delta 文件 | requirement | 丢弃场景数 |
| --- | --- | --- |
| `platform-file-browsing` | 租户与 Agent 工作区文件服务按作用域隔离 | 11 |
| `rbac-authorization` | 固定个人和租户共享资源策略 | 3 |
| `tenant-resource-isolation` | 个人与共享资源有明确边界 | 4 |
| `user-personal-agent-provisioning` | 私属归属与租户边界 | 2 |

**已修复**：把主规范现存的场景补齐进各 MODIFIED 块；对**本 change 有意反转**的场景保留原题、改写结论
（`私有 Agent 的属主与租户管理员`、`写动作不随所有权放宽`、`私有 Agent 的属主与租户管理员仍可读取`），
与 `管理员身份不授予私有内容` 的口径一致。`openspec validate --strict` 现通过。

⚠️ **校验查不到的同类风险**：strict 只比对**场景标题**，不比对 requirement 正文。
整段 MODIFIED 会**静默删除**正文段落。本次已发现并恢复 3 处被丢弃的兄弟正文：
浏览器原生下载段、能力令牌预览段、控制台工作区面板段（`platform-file-browsing`），
以及所有权派生段（`rbac-authorization`、`tenant-resource-isolation`、`user-personal-agent-provisioning`）。
**合并前必须逐段复核正文，不能只依赖 `validate`。**

### 冲突 C3（同 capability 并存）：`tenant-knowledge-console` 被两个 change 同时改写

- 本 change **新增** `私有归属先于知识写入资格`。
- 在途 change `restrict-knowledge-write-authorization` **移除** `知识库写入受权限或租户管理员资格约束`，
  并**新增** `知识库写入按数据根与智能体归属授权`、`知识写入能力按智能体投影`。

两者对「私有知识写入归属」给出口径相反的 requirement，且落在同一 capability 文件。
归档顺序晚者不得整文件覆盖早者；见 C2。

### 冲突 C1（硬冲突）：迁移版本号 `_migration_15` 被两处声明

- 本 change 任务 2.1/2.2 需要在 `auth/store.py` 增加迁移（渠道实例作用域、专属助理来源、绑定挑战与个人关联）。
- `restrict-knowledge-write-authorization` 任务 3.2 **已明文写死**：
  > 3.2 `auth/store.py` 新增 `_migration_15`：把每个角色 `permissions_json` 中的 `knowledge.write` 剥离并 `version+1`…

`auth/store.py` 的注册表当前到 `_migration_14`（`:810`），下一个可用号是 15，**只有一个**。

**处理（2026-09-14 决策，随后因工作树变更而修订）**：**本 change 取 `_migration_16`**，`restrict-knowledge-write-authorization` 保留 `_migration_15`。

> ⚠️ **决策前提已失效（记录留痕）**：决策时 `auth/store.py` 注册表止于 `_migration_14`，故定为「本 change 取 15」。
> 随后复核发现 `_migration_15` **已被对方实现**（`auth/store.py:813`，即剥离 `knowledge.write` 的迁移，`:845` 登记），
> 且对方任务从 1/25 推进到 **24/25**、`_knowledge_write_authorized` 已落在 `channel/web/web_channel.py:347`、
> `knowledge.write` 已从 `auth/policy.py` 移除。**同一工作树在本次会话期间被并发修改。**
> 此时若本 change 再写 `_migration_15`，将出现第二个同名定义并与其迁移语义冲突。
> 故改为**本 change 取 `_migration_16`**，对方无需改动。

- `migration_versions()` 按**位置**派生版本号（`index+1`，`auth/store.py:31-33`），函数名仅是可读性约定；
  但仍须让 `_migration_N` 的编号与位置一致，否则后续维护必然踩错。
- 由于注册表按位置顺序执行且以 `schema_migrations` 去重，**已应用过 15 的库不会再跑新的 15**；
  两处都写 15 时后者的迁移会被静默跳过 —— 这是必须显式分配的根因。
- [ ] **本 change 侧待办**：实现 2.1 时新增 `_migration_16`（渠道实例 `scope`/`owner_user_id`），不得复用 15。

### 冲突 C2（意图冲突）：私有 own 知识写的 tenant_admin 归属

- 本 change 任务 3.5 要求：私有知识 owner 检查**优先于**管理员写分支，非 owner 不得访问他人私有 own 库。
- `restrict-knowledge-write-authorization` 的 delta 按盘点结论**刻意允许** tenant_admin 写他人 own 库。
- 现实现即如此：`channel/web/web_channel.py:1114` 的 `is_tenant_admin` 早返回使 owner 比较永不执行（证据见
  `1-2-authorization-matrix.md` §4.6）。

**处理**：这是两个 change 对同一 requirement 的相反口径，必须在合并时**显式择一**，不能用整文件覆盖。
本 change 的 spec delta 已是最终口径（owner 优先），故 `restrict-knowledge-write-authorization` 的对应文本需在其自身 change 内修订。

### 非冲突：文件级交集

盘点确认两个在途 change 与本 change **无文件级冲突**：
- `allow-personal-memory-tool-execution` 只动 `auth/service.py` 与 `agent/protocol/agent_stream.py`；
- `restrict-knowledge-write-authorization` 动 `channel/web/web_channel.py`、`auth/policy.py`、`auth/store.py`、`agent/personal_assistant.py`、`channel/web/static/js/console.js`、i18n、`scripts/route-baseline.txt`。

但**规范文件相邻**：`openspec/specs/business-permission-catalog/spec.md` 与
`openspec/specs/resource-execution-authorization/spec.md` 当前所载文本均为**未提交的工作树产物**
（分别来自已归档的 `open-tenant-knowledge-console` 与 `allow-tenant-admin-tool-execution`）。
先合并者不得冲掉后者的基线文本。

## 3. `restrict-knowledge-write-authorization`（24/25，**实现已落地**）

> ⚠️ **状态在本次会话期间发生剧变**：初盘时为 **1/25、零实现**；复核时已推进到 **24/25 且代码落地**。
> 本文件其余结论均为该时点快照，凡涉及此 change 的须重新核对。

已落地的实现（与初盘"零命中"相反）：

| 符号 | 位置 | 状态 |
| --- | --- | --- |
| `_knowledge_write_authorized(ctx, agent_id)` | `channel/web/web_channel.py:347` | ✅ 已实现 |
| `_require_knowledge_write(ctx, agent_id)` | `:380` | ✅ 签名已加 `agent_id`，委托给上者 |
| `can_write_knowledge` 投影 | `:8491` | ✅ 已投影 |
| `_migration_15`（剥离 `knowledge.write`） | `auth/store.py:813`，登记 `:845` | ✅ 已实现 |
| `knowledge.write` 退出目录/元数据/tenant_admin 默认集 | `auth/policy.py` | ✅ 已移除（grep 零命中） |
| `clone_agent(..., knowledge_mode=...)` | `agent/admin.py:618` | ✅ 已有该参数 |
| 个人助理以 `own` 模式供应 | `agent/personal_assistant.py:460` | ✅ 已传 `knowledge_mode="own"` |

- 其 delta **仍未合并**进 `openspec/specs/tenant-knowledge-console/spec.md`（该 change 未归档）。
- 其 `Purpose` 的 `TBD` 占位符是否已修、以及最终验收任务是否完成，需在其归档前复核。

→ 门槛 **Q4 的解除条件已基本满足**（该 change 已实现并合入工作树），但因其 delta 尚未合并、且其私有写口径与本 change 的 C2/C3 冲突仍在，
**本 change 的 3.5 仍须按"与已落地代码合并"处理，不能按"等待对方"处理**。

## 4. `allow-personal-memory-tool-execution`（19/22）

- 实现与接线均已就位（`auth/service.py:130-159`、`:1345-1389`；`agent/protocol/agent_stream.py:2133-2168`）。
- 其豁免顺序：`check_resource_action` 失败 → tenant_admin 豁免 → 记忆工具豁免，均 `getattr + callable` 失败即关闭。
- 遗留 3 项声明不在其范围（`HEAD` 既有失败用例、`memory_get` 路径收窄、记忆工具未入平台目录）。

**接口交集**：两者都依赖 `member` 默认集含 `memory.read` 与 `tool.execute`；
`restrict-knowledge-write-authorization` 重写该 requirement 文本时不得回退这两项。

**顺序注意**：`_resource_tool_denial` 中 tenant_admin 豁免先于记忆豁免且置 `ok = True`；
今日无功能冲突（记忆工具 id 不可 grant），但后续若放宽任一豁免须尊重该短路。

## 5. 归档顺序建议

1. `restrict-knowledge-write-authorization`（唯一仍在途；须先实现。其迁移号须与本 change 协商重排，见 C1；
   其私有知识写口径须与 C2/C3 择一）。
2. 本 change 按阶段推进（第 9 章）；三个已归档兄弟的收紧工作由本 change 第 3 章承接。

> 状态：1.1 **完成**。四个兄弟 change 已盘点，冲突 C0/C1/C2/C3 已登记，文件级交集已定。

## 6. `fix-private-agent-owner-reachability`（已归档，全部任务完成）

- 复用入口：`PRIVATE_AGENT_OWNER_ACTIONS = ("read","use")`（`auth/policy.py:376`）、
  `is_private_agent_owner`（`auth/service.py:1408`）、`private_agent_ids`（`:1391`）；
  接线于 `check_resource_action`（`:1287`）与 `resource_ids_for`（`:1448`）。
- 工作台：`_tenant_agent_candidates`（`web_channel.py:8294`）、`_workbench_empty_reason`（`:8345`）、
  `empty_reason` 投影（`:8358`）；前端 `console.js:2087-2312`。
- **刻意边界**：`edit` / `enable` 保持 grant-only，语义为「可达 MUST NOT 蕴含可改」，
  在代码、spec、tasks 三处重复声明。

**本 change 必须反转的测试**（这些测试断言「owner 不能改」，与本 change 任务 3.x 直接冲突）：

| 测试 | 位置 | 现断言 |
| --- | --- | --- |
| `test_owner_cannot_edit_own_private_agent_without_grant` | `tests/test_private_agent_owner_reachability.py:177` | owner 的 `edit` 被拒 |
| `test_owner_cannot_enable_own_private_agent_without_grant` | 同上 `:182` | owner 的 `enable` 被拒 |
| `test_edit_set_excludes_own_private_agent` | 同上 `:187` | `resource_ids_for(...,"edit")` 排除本人私属 |
| `test_owner_cannot_edit_without_a_grant` | 同上 `:327` | `_require_agent_action(...,"edit")` 抛 403 |

## 7. `fix-private-agent-file-scope` + `open-tenant-workspace-console`

**`open-tenant-workspace-console`（已归档，全部完成）**：6 条工作区路由 `closed → tenant`
（`route_registry.py:151-159`，`scripts/route-baseline.txt:30/125-130` 同步）；
`WorkspaceWriteHandler.POST` 走统一 `require_management_write()`（`web_channel.py:10626-10631`）；
前端 `workspace.js:118-148` + i18n `ws_unavailable` / `ws_forbidden`。

**`fix-private-agent-file-scope`（已归档，余 4.3 现场重启验证）**：归属解析层
`_db_file_root_owners`（`:3435`）、`_db_path_owner`（`:3471`）、`_owner_of_db_path`（`:3515`）、
`_db_path_visible`（`:3532`）、`_authorize_db_file_path`（`:3576`）。

### 本 change 必须反转的测试（断言 tenant_admin 可读他人私有文件）

| 测试 | 位置 | 现断言 |
| --- | --- | --- |
| `test_owner_and_tenant_admin_may_read_the_private_agent` | `tests/test_private_agent_file_scope.py:218` | owner 200 **且 tenant_admin 200**（`:230`） |
| `test_member_denied_another_members_private_agent_file` | `tests/test_platform_file_browsing.py:173` | 成员拒绝；`is_tenant_admin=True` 的 ctx `assertTrue(allowed)`（`:191-201`） |
| `test_private_owner_tenant_admin_allowed` | `tests/test_tenant_read_scoping.py:193` | tenant_admin 读不抛错 |
| `test_only_the_owner_and_a_tenant_admin_may_reach_it` | `tests/test_user_personal_agent_provisioning.py:419` | tenant_admin 通过 private-owner 闸门 |

### 新发现缺口：预览令牌无归属复验（本 change 任务 3.4 必须处理）

`PreviewHandler.GET`（`web_channel.py:3672`）**只做路径包含校验**，无身份、无 owner 复验：

```3702:3707:channel/web/web_channel.py
            if os.path.commonpath([full_path, base_real]) != base_real:
                raise web.notfound()
            if not _is_path_allowed(full_path) or not os.path.isfile(full_path):
                raise web.notfound()
```

已核实的后果：为私有目录签发过的旧 `/preview` 令牌在归属收紧后**仍然可用**；
`resolve` 还会为私有目录正常签发 `preview_url`。本 change 的
`非 owner 持有旧私有预览链接` 场景正是针对此点。

### 更正：平台管理员**不是**文件内容旁的旁路

`_db_path_owner_forbidden` 从不检查 `ctx.is_platform_admin`（`web_channel.py:1104-1117` 已逐行确认）。
`is_platform_admin` 只影响**平台根**（`:3445` 加入根、`:3562` 平台根闸门 + 审计），
不构成对他人私有 Agent 工作区的读取资格。内容级唯一旁路是 `is_tenant_admin`（`:1114`）。
