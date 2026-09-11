## Context

见 `proposal.md` 的动机与范围。当前代码接入点（已核对）：

- 唯一的平台资格事实是 `users.is_platform_admin` 列（[auth/store.py](auth/store.py:87)）。服务层有 42 处引用，包含两个语义重复的谓词 `is_platform_admin_user`（[auth/service.py](auth/service.py:646)）与 `is_platform_admin`（[auth/service.py](auth/service.py:1074)），以及 `authorization_mode`（[auth/service.py](auth/service.py:651)）、`_require_platform_admin`（[auth/service.py](auth/service.py:2297)）、`_check_platform_admin_continuity`（[auth/service.py](auth/service.py:2187)）。
- 上下文的三个**生产**构造点从原始列读取：`resolve_context` 的两条分支与 `member_context`（[auth/runtime.py](auth/runtime.py:97)、[auth/runtime.py](auth/runtime.py:129)、[auth/runtime.py](auth/runtime.py:194)）。`revalidate_context`（[auth/runtime.py](auth/runtime.py:138)）也会原样复制旧值，但它在生产代码中**无调用点**——只有 [auth_handlers.py](channel/web/auth_handlers.py:31) 一个 unused import 与两个测试（[test_identity_runtime.py](tests/test_identity_runtime.py:74)、[test_revocation_takes_effect.py](tests/test_revocation_takes_effect.py:102)）引用。
- HTTP 门禁收敛在 `_require_platform_admin(ctx)`（[channel/web/admin_handlers.py](channel/web/admin_handlers.py:42)），覆盖 14 条 `platform` 路由、`/config`、`/api/models` 与 branding 写入。`auth/http_policy.py` 的 `"platform"` 标签本身不做鉴权，仅保证路由方法完整性。
- 另有若干「平台管理员旁路能力」直接读取 `ctx.is_platform_admin`：`web_channel.py` 的功能读权限、`agent.edit`/`chat.use`/`model.use`、agent 名册可见性，以及 `admin_overview.py`、`openai_api.py`、`external_identity.py`、`scheduler/identity.py`。

约束：

- `roles.tenant_id` 为 `NOT NULL REFERENCES tenants(id)` 且唯一键是 `(tenant_id, code)`；`membership_roles` 以 `membership_id` 为键。平台角色无租户、无成员关系，**无法**放入既有 `roles`/`membership_roles`。
- 每次连接设置 `PRAGMA foreign_keys = ON`（[auth/store.py](auth/store.py:529)），且 SQLite 中 `PRAGMA` 在事务内为 no-op，因此迁移不重建 `roles` 表。
- 既有规范 `platform-all-authorization`、`rbac-authorization`、`business-permission-catalog` 明确平台资格是「服务端验证的资格」，且普通角色保存必须拒绝平台标识——本设计保留这些不变量，只改变资格的**存储与解析载体**。
- 既有设计文档 `docs/design/role-resource-authorization-plan.md:64-66` 曾明确「不把平台管理员伪装成……普通角色记录」；本 change 的角色化是新增平台作用域，不进入租户角色表，与该文档的原意（反对把平台资格塞进租户角色）并不冲突，但仍需修订措辞以免歧义。

## Goals / Non-Goals

**Goals:**

- 平台资格由平台作用域内置角色绑定作为唯一来源，可靠枚举、可靠解析。
- 保持全部对外契约不变（`/auth/*` 响应字段、`RequestContext.is_platform_admin`、平台账号 PATCH 请求体、平台侧栏显隐），使迁移对调用方透明。
- 收敛资格写入路径到单一同事务方法，消除「多处直接写列」的分散状态。
- 修正 `revalidate_context` 原样复制旧平台资格的正确性缺陷（见决策 4 的定位说明）。

**Non-Goals:**

- 不删除 `users.is_platform_admin` 列（保留派生镜像，保证旧版本可就地回滚）。
- 不改动前端契约，不在本期把平台角色渲染进「角色权限」页——本 change 是**底层唯一来源重构**，用户在 UI 上看不到变化；「平台管理员以角色形式展示」属独立前端 change。
- 不把平台 all 展开成逐资源 grant，不改动租户业务隔离、私有 owner、业务会话协议。
- 不重构与平台资格无关的授权代码（如 `admin_handlers.py:611` 之外的历史写法）。
- 不删除 `_count_valid_platform_admins` 之外的任何死代码；`_count_valid_platform_admins` 的处置见决策 6。

## Decisions

### 1. 附加式新表，而非改造 `roles` / `membership_roles`

新增 `platform_roles(id, code, name, builtin, permissions_json, version, created_at, updated_at)` 与 `user_platform_roles(user_id, platform_role_id, created_at)`，`platform_roles.code` 唯一。

- **理由**：平台角色无租户、无成员关系，语义与 `roles`（租户作用域）和 `membership_roles`（成员维度）都不同。新增表可让「作用域」在数据模型上显式，且避免在既有表上做破坏性重建。
- **并发说明**：`user_platform_roles` 不设独立 `version` 字段；乐观并发仍由既有 `users.version` 承载（`set_platform_user_status` 校验 `expected_version` 再在锁内写绑定），与现状一致，不引入新的版本维度。
- **备选（未采用）**：把 `roles.tenant_id` 改为可空并加 `scope` 判别列 + 部分唯一索引，在 `membership_roles` 上放宽为可绑用户级角色。缺点是需要重建既有表与索引，且 `PRAGMA foreign_keys` 在迁移事务内无法关闭，子表引用风险高；同时会让租户角色查询必须处处加 scope 过滤，回归面更大。

### 2. `is_platform_admin` 保留为派生镜像，而非立即删除

保留列，唯一写入路径为新的私有 `_set_platform_role(con, user_id, granted, actor_user_id)`，在同一事务内「增删绑定 + 同步镜像」。

- **理由**：`ctx.is_platform_admin` 是 6+ 处能力旁路与全部前端契约的读取点；保留镜像可让这些点**零改动**，把风险集中在迁移与服务谓词。删除列会同时牵动 HTTP、前端、CLI、测试与 4 个旁路模块，且失去就地回滚能力。
- **不变量**：绑定是唯一来源；镜像只由该单一方法写入；不一致时以绑定为准（见 spec 场景「镜像标记与角色绑定不一致」）。
- **写入路径清单（实现必须逐一对齐）**：`bootstrap` 的 INSERT 将镜像列初始化为 `0`（不再硬编码 `1`），随后在同一事务内经 `_set_platform_role` 为初始平台管理员置 `1`；`create_tenant` 与 `add_member` 的 INSERT 继续写 `0`（本就无平台绑定）；`set_platform_user_status` 不再直接 `UPDATE ... is_platform_admin=?`，改走 `_set_platform_role`。迁移的存量回填是唯一的一次性「直接写镜像」例外，且方向是「按绑定驱动镜像」。
- **审计不变**：`set_platform_user_status` 的 `redacted_changes` 仍含 `is_platform_admin` 键，取值来自绑定变更结果，审计事件形状不变。
- **备选（未采用）**：立即删除列并把所有读取改为角色查询。破坏面最大，且旁路能力需要重新设计（权限 or 保留 all 语义），不适合与数据迁移同批交付。

### 3. 平台角色不进租户角色权限并集

`default_permissions_for` / `permissions_for_roles` / `is_admin_role` 保持只识别 `member`、`tenant_admin`；`PLATFORM_ADMIN_CODE` 单独定义，`normalize_permissions` 继续拒绝 `platform_admin` 作为权限 ID。

- **理由**：守住「普通角色不能拼装成管理员」的既有安全边界（`policy.py` 模块注释与 `rbac-authorization` 规范）。平台 all 仍由资格派生，不是角色权限并集的结果。
- **`permissions_json` 的语义（明确）**：`platform_roles.permissions_json` 在本期**不参与任何授权判定**——平台 all 由 `authorization_mode == "all"` 派生，不读该字段。它仅作为「平台角色存在且为内置」的结构性记录，为将来「以角色形式展示平台管理员」或「收紧平台默认权限」预留，seed 时写入 `PLATFORM_ADMIN_DEFAULT_PERMISSIONS` 的显式集合以保持与租户内置角色一致的结构。实现不得声称它已被消费。

### 4. 精确解析；`revalidate_context` 修复为正确性/测试卫生，而非安全漏洞

`resolve_context` 的两条分支与 `member_context` 改为调用服务谓词（以绑定为准，不再读镜像）。`revalidate_context` 从「复制旧值」改为重新派生。

- **理由**：生产环境的撤销即时性**已由 `resolve_context` 每次重读数据库保证**——`revalidate_context` 在生产代码中无调用点（仅 unused import 与两个测试引用），因此「撤销不生效」并不存在于当前生产路径。本修复的定位是**正确性与测试卫生**（让死代码一旦被复用也不会延续旧资格），并提供防御纵深；**不宣称这是修复生产安全漏洞**。保留该修复仍值得做，理由由 spec「平台资格撤销在后续操作生效」的既有 SHALL 支撑。
- **代价**：`revalidate_context` 每次会多一次绑定查询；该函数当前仅测试调用，无生产性能影响。

### 5. 迁移作为附加式 version 6

新增 `_migration_6`：建表与索引 → 幂等 seed `platform_admin` → 按 `is_platform_admin=1` 回填 `user_platform_roles` → 同步镜像。注册进 `_migrations`。

- **理由**：沿用既有 `schema_migrations` 机制（单事务、版本集合幂等），与项目既有迁移风格一致。
- **注意**：seed 与回填必须幂等（`INSERT ... WHERE NOT EXISTS` / `INSERT OR IGNORE`），因为迁移在中途失败的实例上可能重放。
- **`__schema_version__` 死常量**：`__schema_version__ = 3`（[auth/store.py](auth/store.py:74)）仅有定义与注释、**无任何引用**（已核实），是纯死常量。本 change 将其同步为当前迁移数量以消除误导，属**纯清理**，无功能影响；若实现者判断直接删除更合适，可在 tasks 1.4 处记录。

### 6. 连续性统计统一，并处置死方法 `_count_valid_platform_admins`

`_check_platform_admin_continuity` 改为按绑定统计有效平台管理员。`_count_valid_platform_admins`（[auth/service.py](auth/service.py:1084)）经核实**无生产调用点**（此前被误报为「在 2906 行调用」，实际该行是 `_replace_grants_tx`）。

- **决策**：`_check_platform_admin_continuity` 是活代码（被 `set_platform_user_status` 调用），必须改造。`_count_valid_platform_admins` 是死方法，本 change **不强制改造、也不强制保留**——若实现者发现其确无任何调用，可直接删除并在任务记录；若为审慎保留，则一并切换为按绑定统计。二者不得在任务中混为一谈。
- **连续性收紧（记录）**：`_count_valid_platform_admins` 当前只过滤 `active=1`（不排除 `must_change_password`），而 `_check_platform_admin_continuity` 排除未完成改密者。统一到「排除 `must_change_password`」是对连续性口径的收紧；由于前者无生产调用，此收紧**无当前行为影响**，但若前者被保留需保持与后者口径一致。

## Risks / Trade-offs

- [镜像与绑定不一致导致资格漂移] → 绑定为唯一来源；镜像仅由单一方法写入；迁移后校验一致；新增测试断言不一致时以绑定为准。
- [误把平台角色当作租户角色分配，造成提权] → 平台角色不进目录、不进权限并集、`normalize_permissions` 拒绝该 ID；新增规范场景覆盖租户路径尝试授予平台资格。
- [`revalidate_context` 行为改变] → 这是正确性/测试卫生修复，非生产安全修复；生产路径不受影响；测试显式覆盖「撤销后下一请求为 `role`」。
- [迁移重放/部分失败] → 全部 DDL 与 DML 放在单个迁移事务内；seed/回填幂等；启动前置守卫（至少一个有效平台管理员）保持不变。
- [旧版本代码回滚] → 保留 `users.is_platform_admin` 列且不删表，旧代码可就地运行；回退仅需回退读路径。
- [迁移性能] → 回填为单条 `INSERT ... SELECT`，与账号量线性相关，量级为该实例的用户数。
- [范围认知] → 本 change 是底层唯一来源重构，UI 无可见变化；若用户期待「平台管理员在角色页可见」，需另立前端 change（已列入 Non-Goals 与 Open Questions）。

## Migration Plan

1. **阶段门槛（schema）**：`_migration_6` 在同一个 `BEGIN`/`commit` 内建表、seed、回填、同步镜像；失败整体回滚，不留部分 DDL。
2. **启用读路径**：迁移完成后，服务谓词与上下文派生切换到绑定来源；镜像保持同步。此阶段无需 feature flag——绑定与镜像在提交时已一致。
3. **兼容边界**：`/auth/*` 响应、`RequestContext` 字段与平台账号 PATCH 请求体保持不变，前端与旧客户端无需同步升级。
4. **验证门槛**：`openspec validate`；后端身份测试全绿（含平台账号增删、连续性、并发最后管理员、迁移钻取）；前端契约测试通过；新增「撤销后下一次请求不再 all」与「绑定/镜像一致性」回归。
5. **回退**：停止写入新表并恢复旧读路径即可（列与数据均保留）；新表可保留不删，避免二次迁移成本。
6. **文档**：同批修订 `docs/design/role-resource-authorization-plan.md` 中与角色化相冲突的措辞。

## Open Questions

- 是否在「角色权限」页以只读内置角色形式展示 `platform_admin`（属独立前端体验 change，不改变本 change 的规范、方案与任务分解，可在本 change 实施后单独立项）。
