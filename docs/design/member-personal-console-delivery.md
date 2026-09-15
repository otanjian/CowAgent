# 成员个人控制台——交付、迁移与运维说明

日期：2026-09-14。对应 OpenSpec change：`enable-member-personal-console`。
本文是**交付/运维层面**的说明，回答「哪些切片可以启用、如何升级、如何撤下能力、如何恢复、哪些尚未通过」。
行为契约以 `openspec/changes/enable-member-personal-console/specs/` 与主规范 `openspec/specs/` 为准；本文只作操作与证据索引。

---

## 1 一句话结论（可启用范围）

| 切片 | 能力开关 | 默认 | 状态 |
| --- | --- | --- | --- |
| 个人入口与目录（个人页、菜单授权、页面投影） | `member_personal_console` | **开** | 已验收，可启用 |
| 私有智能体生命周期（创建/维护/删除/配额） | `user_private_agent_management` | **开** | 已验收，可启用 |
| 个人记忆写入（`MEMORY.md`、`memory/*.md`、清理与索引） | `personal_memory_write` | **开** | 已验收，可启用 |
| 个人渠道配置（凭据落点、绑定挑战、配额、治理停用） | `personal_channel_onboarding` | **开** | 已验收，可启用 |
| 个人渠道**执行**（真实厂商连接与入站路由） | `personal_channel_runtime` | **关** | **未通过**：无任何类型完成真实端到端验收 |

即：本 change 交付「**配置可保存、连接不开放**」。成员可以保存个人渠道配置并看到明确状态，但不会有真实厂商连接被拉起。

## 2 能力开关（部署侧唯一开关）

五个开关是**独立的**、只控制「是否开放」，**不替代授权检查**：任何一次调用仍然跑 owner / 成员身份 / 功能权限 / 资源授权，关掉开关只会收窄、不会放宽。定义见 `auth/policy.py`
（`PERSONAL_CAPABILITY_SWITCHES` / `PERSONAL_CAPABILITY_DEFAULTS`），部署值读 `config`。

- **只挡「开新」，不挡「关旧」**：创建、保存、绑定、启用这些**开启型写入**在开关关闭时被拒（`capability_disabled`，403）；
  删除、停用、撤销凭据、清空记忆、清空资源参数**始终可达**——撤下开关不能让成员被自己已有的对象锁住。
- **撤回的执行语义**：`personal_channel_runtime` 关闭时，`apply_tenant_instance_runtime` 报 `applied=false, pending=true`
  （「已保存、未连接」）并**先停掉旧连接**；启动合成也不再拉起成员实例。
- **投影一致**：控制台页面投影、连接闸门、入站校验读同一份开关，三者不可能给出互相矛盾的结论；配置不可读时按**关闭**处理（fail-closed）。
- **目录不消失**：撤下开关不会隐藏已验收渠道目录，运营仍能看到「若重新打开会提供什么」。

## 3 支持渠道

- 个人接入目录 `personal_channel_types()` 与租户渠道共用同一份声明（`MULTI_INSTANCE_READY` + 凭据契约），
  当前包含 `feishu`、`dingtalk`、`qq`、`telegram`、`slack`、`discord`、`weixin`、`wecom_bot`；
  每项带 `ready` / `reason` 就绪裁定。
- **配置就绪 ≠ 执行就绪**。执行需要类型进入 `PERSONAL_RUNTIME_ACCEPTED_TYPES`（`channel/channel_instances.py`），
  该集合**当前为空**：只有在真实提供商往返被观察到（任务 7.5）之后才允许登记。
  共享实例承载个人路由另有一份 `PUBLIC_PERSONAL_INGRESS_TYPES`，同样为空。
- 部署可以用 `personal_channel_runtime_types` / `public_personal_ingress_types` 进一步**收窄**集合；
  配置只能收窄，写进集合之外的类型不会把未验证边界重新打开。

## 4 迁移影响

本次变更新增 9 个 schema 版本（`auth/store.py` 迁移 16–24），全部走既有 `schema_migrations` 机制：版本化、逐版本单事务、幂等。

| 版本 | 内容 | 对存量数据的影响 |
| --- | --- | --- |
| 16 | `tenant_channel_instances.scope`（NOT NULL DEFAULT `'tenant'`）、`owner_user_id`、按 (tenant, scope, owner, type, display_name) 的唯一索引 | 存量行全部按 `tenant` 分类，**无需回填**；唯一索引只约束启用中的同名实例 |
| 17 | `agent_bindings.origin`（DEFAULT `'unknown'`） | 存量绑定标 `unknown`，MUST NOT 被当作「系统供应」，因此不会误判为「已供应过」而跳过 |
| 18 | `binding_challenges`、`personal_channel_links` 两张新表 | 纯新增 |
| 19 | `credentials.owner_user_id`（可空）、`personal_resource_configs` | NULL = 租户所有；存量凭据归属不变，解析路径不受影响 |
| 20 | 注册五个个人页并给内置角色写入菜单集合 | 内置角色此前无 `menu` 授权，本次按默认集合写入，避免「只加五个页 → 其余页全被隐藏」 |
| 21 | `tenant_channel_policies`、`tenant_channel_instances.governance_disabled_at/_by` | 无政策行 = 未收窄（不是「拒绝」），升级后行为与升级前一致 |
| 22 | `tenant_private_agent_policies` | 同上：无行 = 未收窄 |
| 23 | `tenant_channel_instances.app_fingerprint`（DEFAULT `''`）+ 启用中的唯一索引 | 存量行指纹为空、不参与冲突判定；仅启用中的 `scope='user'` 行受约束 |
| 24 | `binding_challenges.target_agent_id`、`personal_channel_links.target_agent_id` | 纯新增，用于「每次入站按绑定时选定的目标」而不是事后重推 |

升级为**就地**升级：直接重启即可，无需维护窗口、无需人工清理。`openspec/changes/enable-member-personal-console/evidence/9-4-delivery-drill.md`
记录了幂等升级与「升级中途崩溃可重试」的演练结果。

## 5 运维操作

1. **升级 / 重启**：直接进行。中断后**直接重启**即可续做（版本号与 DDL 同事务，不存在「版本记了、表没建」的中间态）。
2. **撤下某项能力**：改 `config` 中对应开关并重启。已验收目录仍在、owner 检查仍在、成员仍能撤回自己已有对象。
3. **停止个人连接**：撤下 `personal_channel_runtime`。热重启路径与启动路径都会停/不拉个人连接；
   已登记的类型验收不能越过总开关。
4. **恢复（重要）**：**不得**回退到会忽略 `scope='user'`、或会恢复「管理员读取成员私有内容」旁路的旧构建。
   若必须恢复旧版本，只能在维护窗口按**一致备份**恢复，并明确处置本次新增数据
   （个人渠道行、私有 Agent 绑定、个人记忆根、个人资源参数）。恢复后的不变量是：
   **不误启动个人实例**、**不复活管理员私有读取**（演练 D7/D8 已锚定）。
5. **审计**：个人渠道创建/改密/启停/撤销、私有 Agent 创建/删除、记忆清理等均落 `audit_events`；
   配置写入与审计写入在同一事务，审计失败则整笔回滚（不会出现「有对象、无审计」）。

## 6 实施状态与证据索引

| 阶段 | 内容 | 证据 |
| --- | --- | --- |
| 1 | 前置盘点、授权矩阵、既有旁路清单 | `evidence/1-1-sibling-changes.md`、`1-2-authorization-matrix.md`、`1-3-prerequisite-evidence.md`、`1-4-admin-owner-bypasses.md` |
| 2 | 存储与迁移（scope/owner、origin、治理字段、配额） | `evidence/2-0-stage2-migration-design.md`、`2-7-stage2-drill-evidence.md` |
| 3 | 私有内容隔离（记忆、文件、知识、维护） | `evidence/3-6-private-maintenance-acceptance.md` |
| 4–6 | 私有 Agent 生命周期、个人渠道配置与绑定、治理停用 | `evidence/5-5-personal-memory-evidence.md`、`evidence/6-8-personal-channel-evidence.md`，以及 `tasks.md` 各任务下的证据条目 |
| 7 | 个人渠道执行与入站路由（含「无回退」） | `evidence/7-personal-channel-execution.md` |
| 8 | 个人控制台 UI 与三语 i18n | `evidence/8-personal-console-evidence.md` |
| 9.1 | 五个能力开关、入口闸门与启动合成 | `evidence/9-1-capability-switches.md` |
| 9.2 | 逐阶段前置切片复评（四项门槛现状） | `evidence/9-2-prerequisite-evidence.md` |
| 9.3 | 双租户多用户授权回归 + 公共面回归 + 路由基线 | `evidence/9-3-regression-and-route-baseline.md` |
| 9.4 | 幂等升级 / 中断补偿 / 关开关 / 停连接 / 相容恢复演练 | `evidence/9-4-delivery-drill.md` |

## 7 尚未通过的切片（不得声明已开放）

- **个人渠道执行**：`PERSONAL_RUNTIME_ACCEPTED_TYPES` 与 `PUBLIC_PERSONAL_INGRESS_TYPES` 均为空，
  且总开关默认关闭。开启需要先补齐**真实渠道端到端验收**（真实提供商往返、入站身份验证、私有路由不回落），
  并在本 change 内登记类型；在此之前「配置已保存」不等于「渠道已连接」。
- **动作审批（action approval）消费**：个人执行路径尚无真实消费方，属于开启执行前必须补的前置能力（9.2 记录 Q2）。
- **全仓单进程 `pytest` 收集**：因 `scenes/config.py` 与顶层 `config.py` 的同名模块冲突而失败（HEAD 上即存在，104 个收集错误），
  与本 change 无关；本 change 的验证按文件/分组执行（见 9.3 证据）。
- **旧前端断言**：`tests/test_sidebar_account_frontend.cjs` 中 5 个 sidebar account 旧断言在 HEAD 上即失败，非本 change 引入。

## 8 规范口径同步（本次清理）

本次同步清理了规范中残留的「管理员可读取成员私有内容」旧口径，使其与实现一致（均为「仅 owner 正文可达，管理员只经治理接口取得归属与用量元数据」）：

- `openspec/specs/user-personal-agent-provisioning/spec.md`：Purpose 与「私属归属与租户边界」需求及其场景。
- `openspec/specs/tenant-resource-isolation/spec.md`：「个人与共享资源有明确边界」需求，
  并新增「管理员经共享根访问成员私有文件」场景、把「私有 Agent 的属主与租户管理员仍可读取」改为「私有 Agent 仅属主可读正文」。
- `openspec/specs/rbac-authorization/spec.md`：「固定个人和租户共享资源策略」需求及其场景
  （写动作不再要求逐资源 grant，改为所有权派生 + 个人能力开关 + 租户策略约束）。
- `openspec/specs/agent-chat-launch/spec.md`：「显式私有智能体仍然受保护」场景。
- 变更内 delta 同步改名：`tenant-resource-isolation`、`user-personal-agent-provisioning` 中两处仍以「管理员可读」命名的场景。

`openspec validate enable-member-personal-console --strict` 与 `openspec validate --specs --strict`（71 项）均通过。

## 9 与其他 change 的共享面登记（供 `complete-database-capability-parity` 任务 1.2 消费）

| 项 | 本 change 的占用/实现方 | 后续 change 的要求 |
| --- | --- | --- |
| schema 迁移号 | **16–24**（见第 4 节） | 新 change 从 **25** 起编号，不得复用；追加迁移只增不改 |
| 个人记忆 CRUD | `agent/memory/personal.py`（唯一实现，含 `save/list/read/delete/clear` 与 scope generation） | 只调用，不新建第二套 CRUD；列表/正文浏览接同一服务 |
| 个人记忆「清空后防回写」 | `<user_root>/.memory-scope.json`（`generation`/`cleared_at`/`pending_index`）+ `summarizer.py` 的捕获/复核闸门 + `MemoryManager.search` 出口减标签 + `retry_pending_index` | 新入口 MUST 复用该机制；**索引层（`chunks`/FTS5/向量）没有 tombstone**，所以「清空后索引行尚存」由 `pending_index` 屏蔽与重试覆盖，新入口不得绕过它直接读索引 |
| 个人渠道 schema 与服务 | `tenant_channel_instances`（`scope`/`owner_user_id`/`app_fingerprint`/`governance_disabled_*`）、`binding_challenges`、`personal_channel_links`、`auth/service.py::*_personal_channel_*` | 只是提供方适配器，复用同一实例与绑定服务，禁止复制 schema 或自行绑定任意外部 subject |
| 个人资源参数与凭据引用 | `personal_resource_configs` + `credentials.owner_user_id` | Desktop/Web 入口复用同一后端 |
| 路由策略 | 9 条 `personal` 策略路由（`/api/memory/personal*`、`/api/personal/channels*`、`/api/personal/resources*`），登记于 `scripts/route-baseline.txt` 与 `tests/test_route_registry.py::PersonalConsoleRouteTests` | 新增个人路由同样按 `personal` 策略登记；不得把管理面（`/api/channels`、`/api/tenant/channels`、`/api/agents`）改成 `personal` |
| 个人菜单迁移 | 迁移 20（五个 `personal.*` 页 + 内置角色菜单集合） | 复用迁移结果，不重复注册页面 |
| 共享前端文件 | `channel/web/static/js/console.js`、`channel/web/static/js/i18n/*`、`tests/fixtures/console_i18n_snapshot.json` | 改动后必须同步 i18n 快照与 `test_console_i18n_parity.cjs` |
| 归档顺序 | 本 change **先**归档（其能力是 1.2 / 5.5 / 11.2 / 11.3 / 11.4 的前置） | 兄弟 change 需等本 change 的任务与证据落定，并对未通过切片保留「未覆盖」表述 |

**知识写授权现状（对齐兄弟 change 任务 1.2 的「不依据陈旧提案」要求）**：
`restrict-knowledge-write-authorization` 已归档并落地，知识写入按**数据根归属 + 智能体归属**判定，
`knowledge.write` 已从权限目录移除；本 change 的私有知识写入切片因此已具备前置，不存在待实现的写授权依赖。

**记忆索引现状（供兄弟 change 任务 5.5 / 11.2 参考）**：`chunks` / `chunks_fts` / `chunks_fts_trigram` /
向量后端**都没有墓碑（tombstone）或 scope 版本列**，删除是硬删（`delete_by_path`）。
本 change 的「清空后防回写」不依赖索引墓碑，而是三处配合：清空**先**递增 `.memory-scope.json` 的
`generation`、把待清标签记录为 `pending_index`（索引清理失败可重试）、并在写入出口复核 generation
（`write_daily_summary`）与在检索出口减去 `pending_index` 标签（`MemoryManager.search`）。
复核发现：`dreams/` 与 `evolution/` 日志**有意不受清空影响**（非用户撰写的条目），这是 5.4 的明确边界，
不是缺口。兄弟 change 若重新开放 `/api/memory` 的列表/正文，必须复用同一服务与同一屏蔽机制，
不要新建读索引的旁路。
