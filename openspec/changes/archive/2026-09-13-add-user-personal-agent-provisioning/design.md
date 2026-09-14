## Context

动机见 `proposal.md` §Why。当前代码的接入点：

- **成员创建**：`channel/web/admin_handlers.py` 的 `TenantMembersHandler.POST`（`_require_management_write` + `_require_tenant_admin`）调用 `auth/service.py:IdentityService.create_member`，后者在**单个 SQLite 事务**内写用户、成员关系、角色与审计。它当前不知道任何智能体概念。
- **智能体克隆**：`agent/admin.py:AgentAdminService.clone_agent(source_agent_id, agent_id, workspace=...)` 已实现「1:1 复制人设与配置、只换身份」；`clone_agent` 与 roster 写入都不参与身份库事务（`team.json` 是另一份存储）。
- **租户内 agent 归属**：`agent_bindings`（主键 `agent_id`，含 `private_owner_user_id`、`cloned_from_agent_id`），由 `IdentityService.bind_agent` 写入；`private_owner_user_id` 的独占读取门禁在 `channel/web/web_channel.py` 的 `_db_path_owner_forbidden` / `_require_private_owner`。
- **缺省解析**：`IdentityService.resolved_default_agent_id(tenant_id)` 单点实现「配置默认 → 租户共享最小 id → 任意可用绑定」，Web 层 `_resolve_tenant_default_agent(ctx)` / `_tenant_default_agent_id(ctx)` 委托它，全仓共 6 处调用点，均持有 `ctx`。
- **既有复制编排**：`agent/tenant_provisioning.py:TenantAgentProvisioner` 演示了「身份库与 roster 无法同事务提交 → 逐智能体处理 + 失败补偿 + 来源承接默认」的成熟模式，本 change 复用它已验证的规划口径而不是另发明一套。

约束：需求基线是 `openspec/specs/`；不改 `agent_bindings` 表结构；不新增 HTTP 路由；不改变租户默认的「必须租户共享」语义。

## Goals / Non-Goals

**Goals:**

- 新成员自动获得一份**私属**的「智能办公助理」，工作区独立、人设指向本人。
- 该智能体成为**该用户个人**的缺省锚点，租户默认与其它成员的入口完全不受影响。
- 身份库与 roster 两步写入的不一致被局部收敛（补偿 + 幂等重试），成员创建不被拖垮。
- 存量数据零迁移成本：没有个人默认登记的成员，缺省解析行为与今天逐字节一致。

**Non-Goals:**

- 不做控制台 UI（成员页不新增按钮、不新增个人默认的可视化编辑；登记由创建动作写入）。「个人偏好默认智能体」的可视化编辑不在本 change。
- 不做个人助理的「共享给租户」入口——那属于产品规划 3.3（后续版本），且既有 `make_agent_tenant_shared` 已提供底层动作。
- 不改平台管理员创建租户管理员的路径（`create_tenant_admin_account`）；不改租户默认智能体的任命/共享转换规则。
- 不做 `{{PLACEHOLDER}}` 式人设模板机制；个人化只做 `USER.md` 改写 + 别名替换。
- 不迁移/不回填既有成员的个人助理（不批量给存量用户补建）。

## Decisions

### D1 用户级默认放在 `memberships.default_agent_id`，不新建表

**方案**：迁移 9 给 `memberships` 增加可空列 `default_agent_id TEXT`。它是 (tenant_id, user_id) 边上的属性，写入用 `UPDATE memberships SET default_agent_id=? WHERE tenant_id=? AND user_id=?`。

**为什么不新建 `user_default_agents` 表**：键就是 (user_id, tenant_id)，与 membership 一一对应；新表要自己维护外键与级联，还会在成员停用时留下孤立行。列在 membership 上，停用/删除成员天然一并失效。

**为什么不用「用户拥有的私属智能体」隐式推导**：`register_default_tenancy` 会把存量智能体打上初始管理员的私属归属，隐式推导会让这些管理员的缺省入口悄悄漂移到 legacy 智能体上；而且「私有」不等于「我要默认它」。显式登记把「哪个是我的默认」与「谁拥有它」解耦。

**版本列说明**：写入**不** bump `memberships.version`。租户编辑器的批量提交用 version 做乐观并发，创建个人助理是与其无关的旁路动作，bump 会让编辑器的草稿失效。这与 `set_tenant_default_agent` 对 `tenants.version` 的既有处理一致，需在代码注释中说明。

### D2 个人助理克隆不写 `cloned_from_agent_id`

`agent_bindings` 上有偏唯一索引 `idx_agent_bindings_clone_source(tenant_id, cloned_from_agent_id) WHERE cloned_from_agent_id IS NOT NULL`。个人助理对同一来源在**同一租户内会有多份**（每个用户一份），写该列会让第二个用户的克隆撞唯一约束并 409。

因此本 change 的绑定不带 clone 溯源；幂等性改由「该用户在本租户是否已有私属绑定」判定（`private_owner_user_id == user_id`）。代价是失去了「来源 → 克隆」的机器可读溯源，改为在审计事件里记录来源标识。**备选**：把偏索引改成含 `private_owner_user_id` 的三列索引——会改动既有复制流程的幂等语义，风险大于收益，否决。

### D3 编排落在新模块 `agent/personal_assistant.py`，与身份服务单向依赖

身份服务（`auth/service.py`）不能 import `agent.admin`（会形成循环并让身份库测试被 roster 拖住）。做法与 `tenant_provisioning.py` 同构：

- `IdentityService.create_member` 在**身份事务提交之后**调用一个注入式钩子（默认实现 lazy import 新模块，可用参数/工厂在测试中替换）。
- 新模块负责：解析来源 → 规划 id 与工作区 → `clone_agent` → `bind_agent(private_owner_user_id=本人)` → 人设个人化 → 写个人默认登记 → 审计；任一步失败则 `_compensate`（删 roster 条目 + 删工作区）并返回失败明细。
- `create_member` 把该结果放进响应（`personal_agent: {status, agent_id?, reason?}`），**不**把异常上抛。

**备选**：把编排写进 `create_member` 内部。否决——一个方法里混两种存储的事务边界与补偿逻辑不可测试、不可复用。**备选**：把身份库写入也搬到编排层（先建 roster 再建成员）。否决——成员创建是核心路径，顺序反转会让「智能体已建、成员失败」成为新的不一致方向。

### D4 来源 = 本租户内按名称解析的「智能办公助理」，缺源跳过

解析规则（按序）：配置显式 id（须已绑定本租户）→ 配置的名称（默认「智能办公助理」）在本租户已绑定 roster 中匹配，取启用者、多个时取稳定标识最小。找不到或唯一匹配已停用 → 跳过。

跳过而非失败：成员创建是管理员的高频核心动作，不能因为一个模板缺失而阻断建人；跳过原因（`no_source_agent` / `source_disabled` …）进响应与审计，可诊断。

**为什么按名称**：实测两个租户的来源名称都恰好是「智能办公助理」（id 分别是 `my-assistant-admin` 与 `my-assistant-admin-test15`），名称是跨租户唯一稳定的锚；名称可配置以便客户改叫法。**备选**：按 id 前缀猜（脆弱）、取租户默认（语义错位，会把经营分析参谋变成个人助理），均否决。

### D5 人设个人化 = `USER.md` 改写 + 别名替换

- `USER.md` 直接改写为新成员的资料（显示名 / 用户名 / 岗位 / 部门）。这是本系统里「我在为谁服务」的权威文件（`_seed_user_profile` 的既有语义），必须换掉，否则新用户的助理仍以为自己在服务前一位所有者。
- 别名替换：配置 `personal_assistant_owner_aliases`（默认 `["admin"]`）中的每个标识，在克隆件的 `AGENT.md` 与描述类字段（`description` / `persona_summary` / `greeting` / `position`）中按**整词**替换为新成员显示名。只改克隆件，源文件不动；别名未命中即保持原文，MUST NOT 做模糊猜测。
- **备选**：源人设改用 `{{OWNER}}` 占位符。更精确，但要先改所有人的源人设，且对存量源无效；作为后续增强更合适。

### D6 缺省解析插入用户级优先层，保持只读

`resolved_default_agent_id(tenant_id, user_id=None)` 新增第一层：

1. `user_id` 非空时读 `memberships.default_agent_id`（须属于本租户、且 `_agent_is_usable`）；
2. 该层还须通过**可到达性**判定：该绑定的 `private_owner_user_id` 为空（租户共享）或等于 `user_id`。这保证「他人私有智能体不被锚定」不被本次改动破坏；
3. 之后完全走既有三层。

`user_id` 缺省为 None 时行为与今天逐字节一致（例如平台级/系统调用），存量调用方不受影响。Web 层 6 处 `_resolve_tenant_default_agent(ctx)` / `_tenant_default_agent_id(ctx)` 改为透传 `ctx.user_id`。

只读约束保持：解析路径 MUST NOT 写登记；失效的个人默认只跳过并告警，不清理（清理只在删除动作里做）。

### D7 失效清理并入 `release_deleted_agent`

该方法是「智能体被删除」的唯一收敛点（既清 `tenants.default_agent_id`、删 `agent_bindings` 行，也不限定调用方租户）。新增：同一事务内把 `memberships.default_agent_id` 等于该 agent 的行置空，并把数量计入审计。停用不触发清理——解析层会跳过不可用项，保留登记让重新启用后自动恢复。

## Risks / Trade-offs

- **两个存储无法同事务**：roster 写入成功而绑定失败会留下未绑定条目 → 逐用户补偿（删条目 + 删工作区）使其可安全重试；补偿本身 best-effort，失败只记日志，下一次同用户创建时由「私属绑定存在性」判定幂等而非重复建。
- **克隆语义与 `clone_agent` 的 USER.md 播种冲突**：`clone_agent` 会从默认智能体播种 `USER.md`，而我们要的是新成员资料 → 在克隆后**覆写** `USER.md`，顺序必须固定（克隆 → 个人化），并在测试里断言最终内容。
- **名称解析的脆弱性**：客户把来源改名或停用会让新用户拿不到个人助理（不报错，只跳过）→ 跳过原因可诊断 + 配置可显式指定 id；管理端响应里回带原因。
- **工作区数量增长**：每个用户一份工作区，磁盘与 roster 条目随成员数线性增长 → 这是「个人空间独立」的必然成本；本 change 不做上限（`resource-quota` 是另一责任域，后续可在配额层介入）。
- **人设别名替换可能误伤**：别名是自由文本（默认 `admin`），整词替换可能命中无关词 → 默认别名只含 `admin` 这一类所有者标识，且替换范围限定在少数文本字段；MUST NOT 跨字段做模糊匹配。可配置为更精确的别名。
- **`private_owner_user_id` 使普通成员看不到来源**：个人助理是私属的，普通成员若同时看得到租户共享的同名来源，列表中会出现两个「智能办公助理」（同名，一个共享一个私属）→ 接受的取舍：这是「同一角色、个人化一份」的产品表达；名字区分留给后续 UX 迭代，本 change 不做重命名。

## Migration Plan

1. 迁移 14（`auth/store.py::_migration_14`）：`ALTER TABLE memberships ADD COLUMN default_agent_id TEXT`（可空、无默认值、无回填）。迁移框架按 schema_migrations 幂等执行，失败可重跑。已在真实 `identity.db` 的副本上验证：升级前 13 条 → 升级后 14 条，成员行与租户默认逐字节不变，存量成员该列为 NULL，重复重跑不报错。
2. 存量数据：所有既有成员 `default_agent_id IS NULL` → 缺省解析跳过用户级层，行为与升级前一致；无需回填、无需停机。
3. 回滚：停止调用编排即回到旧行为（登记列成为死列，不影响解析）；`release_deleted_agent` 的追加清理是幂等的，回滚无需数据修复。
4. 配置新增项均有内置默认值，未配置即按「智能办公助理」+ 别名 `["admin"]` 工作。

## 实施参数（实现期已确认）

- **配置键**（`config.py`，均有内置默认值）：
  - `personal_assistant_agent_name` —— 来源智能体名称，默认 `"智能办公助理"`；
  - `personal_assistant_agent_id` —— 来源显式 id，默认空（按名称解析）；
  - `personal_assistant_owner_aliases` —— 源人设所有者别名表，默认 `["admin"]`，支持列表或逗号分隔字符串。
- **新 id 派生规则**（`agent/personal_assistant.py::_plan`）：`<来源 id>-<username>`，经 `_sanitise_agent_id` 净化并截断至 `_MAX_AGENT_ID_LEN`（64），冲突时按 `-2/-3…` 递增，且仅在「roster 有同名条目 + 无绑定 + 工作区路径一致」时判定为可收养（adopt）。
- **审计动作名**：`member.personal_agent.create` / `member.personal_agent.skip` / `member.personal_agent.fail`，payload 仅含 `user_id` / `agent_id` / `source_agent_id` / `reason`。
- **成员创建响应**：`create_member` 返回值新增 `personal_agent`（`{status, agent_id?, source_agent_id?, reason?}`），`TenantMembersHandler.POST` 经 `{"member": result}` 原样回带，不新增路由（`scripts/route-baseline.txt` 不变），前端无需改动。
