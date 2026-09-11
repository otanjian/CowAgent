## Context

动机与范围见 `proposal.md`；行为契约见 `specs/tenant-agent-provisioning/spec.md` 与 `specs/tenant-management/spec.md`。以下是实现必须面对的现状约束（均已核对代码）：

- 智能体是全局实体：注册表 `AgentRegistry`（`agent/registry.py`）由 roster `team.json` 驱动，`AgentProfile` 携带 id/名称/工作区/人设与数字员工字段；工作区是宿主目录。
- 绑定严格一对一：`agent_bindings(agent_id PRIMARY KEY, tenant_id, private_owner_user_id)`（`auth/store.py` 迁移 1）；`IdentityService.bind_agent()` 拒绝跨租户改绑，跨租户只能"新建实体 + 新建绑定"。
- 已有克隆原语：`AgentAdminService`（`agent/admin.py`）的 `create_agent(clone_from=...)` 会新建 id 与工作区、只拷 `CLONED_FILES`（`AGENT.md`/`USER.md`/`RULE.md`/`BOOTSTRAP.md`），并 `_seed_name`；roster 写入经 `_commit` 的 `revision` 保护，异常时 `rmtree` 新建目录。注释明确"整树拷贝在每个方向都是错的"（默认智能体工作区就是实例根，含其他智能体与共享库）。
- 状态目录分层：`common/state_dir.py` 的 `shared_root()` 在 database 模式按当前身份的租户解析；`skills_dir()/knowledge_dir()` 走"存在即私有，否则共享"规则；租户根之间有 `validate_tenant_shared_root()` 的互不包含校验。
- 租户编辑器现状：`channel/web/static/js/identity-admin.js` 的整页编辑器固定四标签（basic/model/tool/admin），由一次提交按 `admin → grants → basics` 顺序提交脏标签，统一弹一次近期密码（`tenant-password-modal`）。
- 平台接口现状：`channel/web/admin_handlers.py` 提供 `/api/platform/tenants*`，`_require_platform_admin` + `_recent_password` + `auth/http_policy.py` 的 `"policy": "platform"` 登记；路由在 `channel/web/web_channel.py` 顶部 `urls`。默认租户用 code 默认值 `default` 由 `management bootstrap` 创建，注册表智能体经 `register_default_tenancy` 绑定到它。
- 运行时热重载已有入口：`web_channel._reload_agent_runtime(service, changed_agent_ids)`。

## Goals / Non-Goals

**Goals:**

- 提供"从默认租户向指定租户克隆智能体"的可测、幂等、可恢复的实现路径，横跨 roster/工作区（`AgentAdminService`）与绑定/审计（`IdentityService`）。
- 让租户智能体首次在编辑器里可见（只读列表），复制是可发现的一等动作。
- 复制边界严格：只带行为与配置，不带记忆、会话、凭据、共享实体文件与宿主路径。

**Non-Goals:**

- 不做租户智能体的增/删/改/绑定/解绑的完整管理（本次只做只读列表 + 候选勾选复制）。
- 不做源租户的任意选择（来源固定为"默认租户"，见 proposal）；勾选只决定复制哪些来源智能体，不改变来源解析。
- 不做跨租户共享智能体（不放宽一对一绑定）。
- 不复制技能/知识实体内容，不做运行中会话迁移。
- 不引入运行开关；仅在 database 身份模式启用。

## Decisions

### D1 新增独立编排模块，不塞进既有服务

新增 `agent/tenant_provisioning.py` 作为唯一编排点：读取来源绑定（身份域）→ 克隆实体（Agent 域）→ 写绑定与映射（身份域）。它不 import web 层，运行时热重载由 HTTP 层触发。

- 备选：把方法挂到 `AgentAdminService`（它已拥有 roster/工作区）。否，因为它不应依赖 `IdentityService`，会制造 agent→auth 的耦合，也让单测必须拖起身份库。
- 备选：挂到 `IdentityService`。否，身份域不应写 roster 与文件系统。

### D2 来源解析锚定全局默认智能体的绑定

`source_tenant = svc.get_agent_binding(registry.default_agent_id)['tenant_id']`；缺失时回退到 `list_agent_bindings()` 中绑定数最多的租户；仍无法确定则报可操作错误。可复制候选 = 该租户 `agents_for_tenant()` 的全部智能体（含停用），每项附"是否已有克隆 + 克隆标识"（由 `(target_tenant, source_id)` 的来源映射反查）。

- 备选：按 `tenant.code == "default"` 判定。否，code 可被改名/占用，且真实数据锚点就是默认智能体的绑定。
- 备选：引入 `tenants.is_default` 标记。否，需要迁移与回填，收益不抵复杂度。

### D2b 复制以显式选中集合为准，校验在写入之前

POST 必须携带非空 `source_agent_ids`：空集合 → 400 且不写入；任一标识不在来源候选内 → 400 且**整体**拒绝（先全量校验再开始逐项处理，避免"部分写入 + 报错"这种最难恢复的状态）。未选中的候选一律不复制。

- 备选：空集合视为全量复制。否，误触会把整个默认租户的智能体灌进目标租户，风险不对等。
- 备选：逐项校验、跳过无效项继续。否，会让"点了 5 个只复制了 3 个还返回成功"，与"不误报成功"的要求冲突。

### D3 标识与工作区生成规则

- 新 id：`<source_id>-<target_code>`，按 `_AGENT_ID_RE`（`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`）清洗并截断到 ≤64；与全局任一已注册 id 冲突时追加 `-2`/`-3`…
- 工作区：`<target_tenant.shared_root>/agents/<new_id>`。目标租户根与默认租户根互不包含（`validate_tenant_shared_root`），因此该路径既落在目标租户自己的隔离根内，又不与任何既有智能体工作区重叠，满足 `_reject_overlapping_workspace` 的既有约束。
- 另起独立 id 而非复用，是因为 roster 全局唯一，复用会与来源智能体冲突。

### D4 复制内容复用既有克隆原语

落盘走 `AgentAdminService._bootstrap_workspace` + `_clone_persona`（`CLONED_FILES`）+ `_seed_name`；`AgentProfile` 的配置属性从来源 profile 逐字段构造（描述、头像、model/bot_type、skills/knowledge 选择、position/category/tags/greeting/persona_summary/scene_id/knowledge_ids/sops/tools_allowlist/tools_denylist）。知识模式按来源的工作区实际形态复刻：来源为"自有"则建独立空知识库，否则保持共享（在目标身份下解析为**目标租户**的共享根，天然隔离）。

- 备选：直接 `shutil.copytree`。否，会自动带入记忆/会话/凭据，且默认智能体工作区是实例根，递归无界（既有注释已说明）。
- 备选：抽取一个公共 `clone_profile()` 到 `agent/admin.py` 供两处复用。可行，实现时优先做小步抽取而不是复制粘贴；但 MUST NOT 改变 `create_agent` 的既有外部行为。

### D5 幂等靠绑定上的来源映射

迁移 `_migration_8`：`ALTER TABLE agent_bindings ADD COLUMN cloned_from_agent_id TEXT`，并建部分唯一索引 `CREATE UNIQUE INDEX ... ON agent_bindings(tenant_id, cloned_from_agent_id) WHERE cloned_from_agent_id IS NOT NULL`。复制前查 `(target_tenant, source_id)` 是否已有克隆，有则跳过。`bind_agent()` 增加可选 `cloned_from_agent_id`（默认 None，保持既有调用兼容）与可选 actor（用于审计）。

- 备选：独立 `agent_clones` 表。信息量相同但多一套仓储代码，且该映射与绑定同生命周期（解绑即失效），放绑定行上更贴近。
- 备选：按 id 命名约定判断幂等。否，改名/改 id 会失效，spec 明确禁止。

### D6 逐智能体事务化 + 补偿，返回部分结果

每个来源智能体独立处理，顺序为**工作区 → roster → 绑定**：

1. 落盘工作区（尚不可见）；
2. `AgentAdminService._commit` 写 roster（revision 保护）；
3. `bind_agent(..., cloned_from_agent_id=source_id)` 写绑定、映射与审计。

任一步失败则补偿：`rmtree` 新建工作区；若 roster 已写则移除该条目并重新 `_commit`。已成功的其它智能体不回滚。响应 `{copied, skipped, failed:[{source_agent_id, error}], default_agent_id}`。

- 崩溃窗口：进程在第 2、3 步之间挂掉会留下"在 roster 但未绑定"的智能体。缓解：重跑时对 `id == <source_id>-<target_code>` 且无绑定的残留条目按来源映射规则收养并补绑（或清理），该恢复策略在实现期定死并加测试。
- 备选：先绑定后建实体。否，会让绑定短暂指向不存在的智能体，运行时按 id 解析会炸。备选：单事务包住 DB 部分。绑定与 roster 不在同一库（roster 是文件），做不到原子，补偿是唯一选择。

### D7 目标默认智能体承接

若复制前 `agents_for_tenant(target)` 为空且本次有新增克隆，则写 `tenants.default_agent_id`（一次 `UPDATE` + 审计）：优先来源默认智能体的克隆；若该来源智能体未被选中（因而没有克隆），取本次第一个成功新增的克隆。响应回带 `default_agent_id`。

- 为什么需要"回退到第一个新增克隆"：既有解析规则（`_require_session_owner` / `_tenant_default_agent_id`）在"多绑定且无配置默认"时判定为歧义并拒绝。若三个克隆落地但都非来源默认，目标租户在未显式选智能体时将不可用，与"复制后运行时可用"冲突。
- 该写入改变的是目标租户的"默认指向"而非绑定，不参与幂等判定；目标复制前已有智能体或已有默认则完全不动。

### D8 授权、近期密码与审计

- 路由：`/api/platform/tenants/([^/]+)/agents`，`http_policy` 登记 `GET`/`POST` 为 `platform`；handler 走 `_require_platform_admin`，POST 走 `_recent_password`。
- `GET` 返回 `{agents:[...], copy_source:{tenant_id, code, name, candidates:[{id, name, enabled, already_copied, clone_agent_id}]}}`（来源不可解析时 `copy_source: null` 并附原因）。字段裁剪沿用既有投影思路，MUST NOT 出 workspace/宿主路径。
- `POST` body：`{action: "copy_from_default", source_agent_ids: [...], recent_password}`。校验顺序：平台管理员 → 近期密码 → 来源可解析 → 选中集合非空 → 每个标识属于候选 → 才开始逐项写入。
- 审计：每个绑定已有 `agent.bind`；本操作再加一条汇总 `agent.copy_to_tenant`（来源租户、目标租户、选中/新增/跳过/失败计数与涉及 id），脱敏。既有 `bind_agent` 的 actor 为 None，本路径传入操作者以保留可追溯性。
- 越权/校验失败走既有 403/400 与 `result=denied` 审计口径。

### D9 前端：第五个标签 + 批量步骤

`identity-admin.js` 编辑器标签集合扩为 `basic/model/tool/agent/admin`，即「智能体」插在「工具授权」与「租户管理」之间（既有四个标签的相对顺序不变，只新增一个）。新标签三段内容：目标租户已绑定智能体（只读，含默认标记）+ 默认租户候选（复选框，已复制的候选呈"已同步"且不可重复勾选）+ 结果回显（新增/跳过/失败明细与承接的默认智能体）。勾选后该标签变脏，由编辑器既有的一次提交与统一密码弹窗提交，作为一个新的批量步骤（放在 grants 之后、basics 之前；它不改变租户 version，不参与版本串接）。未勾选即提交时前端不发请求并给出可操作提示；失败时保留可重试错误，且不显示整体成功。空列表与读取失败区分渲染。三语 i18n 与 `console.css` 样式对齐既有标签；顺带补上该编辑器缺失的 `admin_back_to_list` 三语条目（当前直接渲染裸 key）。

- 备选：用一个独立的即时按钮直接调用接口。否，会绕过编辑器统一密码与批量语义，且与"每个标签独立提交/一次提交"的既有契约不一致。

## Risks / Trade-offs

- [跨库非原子] roster（文件）与绑定（SQLite）无法同事务 → 用逐智能体补偿 + 可续跑把不一致窗口压到单个智能体；实现必须补"孤儿收养/清理"与对应测试。
- [`_commit` 的 revision 竞争] 复制期间有其它 roster 编辑会触发 `StaleRosterError`。→ 逐智能体读取-写入并让该智能体失败进入 failed 明细，整体不误报成功；不重试覆盖他人编辑。
- [命名约定 id 与来源 id 撞车] `<source_id>-<target_code>` 可能超长或已被占用。→ 清洗、截断、追加序号；以全局注册表为准判重，并加冲突测试。
- [目标租户根尚不可写] 目标 root 未就绪/无权限会让所有智能体失败。→ 失败明细具体到智能体与原因；因逐智能体处理，不会产生半成品 roster 条目。
- [误触全量] 勾选式若在空集合时退化为全量，会把默认租户所有智能体灌入目标租户。→ D2b 明确空集合 400 拒绝，前端也不发请求，并有对应测试。
- [部分写入 + 报错] 若逐项校验选中项，会出现"部分成功但整体报错"。→ D2b 先整体校验再开始写入，越界标识不产生任何写入。
- [共享知识语义] 克隆在目标租户共享根下解析共享技能/知识，若目标租户根尚无对应目录，克隆可能"看不到技能"。→ 与既有租户初始化一致（技能/知识由 state_dir 按需搭建共享副本），本 change 不改变该规则；如需预置资产，属后续切片。
- [默认智能体承接与既有约定] 目标为默认租户自身时复制无意义。→ 拒绝 `target == source`，返回可操作错误。

## Migration Plan

- 迁移 `_migration_8` 幂等追加：加列 + 部分唯一索引；旧库升级后既有绑定 `cloned_from_agent_id` 为 NULL，索引对 NULL 不生效，不影响既有数据。
- 回滚：代码回滚即可；列与索引保留无副作用（旧代码不读该列）。MUST NOT 提供"删除克隆"的隐式回退，删除走既有 `delete_agent` + 解绑路径。
- 启用门槛：仅在 database 身份模式提供路由；legacy 模式 handler 走 `_guard_database` 拒绝。发布后按 spec 的场景逐条验收，并产出 `evidence.md`。

## Open Questions

Implementation-time findings (resolved; kept here so the reasoning is not lost).

- 目标租户根下 `agents/` 子目录是否需要在复制前预创建并计入租户空间"就绪"判定。→ **无需预创建，就绪判据不变**。`clone_agent` 落盘走 `_bootstrap_workspace`（`ensure_workspace(..., create_templates=True)`），父目录按需创建；因此判据仍是"租户根存在"，本 change 不改 `_tenant_space_public`。实测：目标根下无 `agents/` 时复制成功，工作区落在 `<target.shared_root>/agents/<id>`（`tests/test_tenant_agent_copy_acceptance.py::IsolationAcceptanceTests::test_every_clone_workspace_is_inside_the_target_shared_root`）。
- 克隆 id 的展示别名（是否在名称上加租户后缀）。→ **保留来源名称，不加后缀**。`clone_agent` 未传 `name` 时由 `_seed_name` 沿用来源 `AGENT.md` 中的名称；可读性差异由候选列表已展示的 `source_agent_id` 承担。实测：`tests/test_tenant_agent_copy_acceptance.py::IndependentEvolutionTests::test_editing_the_source_leaves_the_clone_alone` 断言来源改名后克隆名仍为 `Alpha`。
- 既有约定的一处偏差（实现期核对代码时发现，已在 `evidence.md` 记录）：`_resolve_tenant_default_agent` 在"多绑定且无配置默认"时**不再拒绝**，而是确定性地取最小 id（`channel/web/web_channel.py`，另一 change 引入）。因此 D7 的"否则目标不可用"只是动机之一：本 change 仍显式写入 `tenants.default_agent_id`，让承接结果可被审计、可被后续维护覆盖，而不是依赖隐式最小 id。
