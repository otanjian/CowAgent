## Context

现状（动机见 proposal.md - Why，需求见 `specs/tenant-management/spec.md`）：

- `tenants` 表当前只有 `id / code / name / active / shared_root / default_agent_id / version`，没有删除或归档概念；`tenant-management` 规范明确「MUST NOT 修改稳定 ID/code 或删除、归档租户」。
- 「租户失效」的既有唯一机制是 `active=false`：`auth/runtime.py` 的 `resolve_context` / `member_context`、`auth/service.py` 的 `_active_tenants_for` 与租户能力读取、`common/state_dir.py` 的 `private_root`、`agent/tools/todo/todo_tool.py` 等都在检查 `tenant["active"]`；这些是「某租户当下是否可被业务使用」的权威判定点。
- 平台写操作已有一套统一契约：`_require_management_write()`（Origin/CSRF）+ 平台管理员 + 近期密码（`recent_password`）+ `expected_version` 乐观锁 + 同事务审计与版本递增（见 `set_tenant_status` / `set_tenant_profile`）。
- 迁移由 `auth/store.py` 的 `_migrations` 列表按版本顺序幂等执行（当前到 `_migration_11`）。
- 前端租户列表在 `channel/web/static/js/identity-admin.js` 的 `loadTenantView()` 渲染，行操作当前只有「编辑」；通用确认弹窗 `openAdminModal(cfg)` 已支持文本 / 密码字段与提交回调。

## Goals / Non-Goals

**Goals:**
- 以软删除（归档）实现「删除租户」按钮，可逆且不丢数据。
- 归档复用 `active=false` 作为失效机制，使既有所有业务门自动生效，最小化改动面。
- 归档/恢复满足平台写操作的既有鉴权、近期密码、乐观锁、审计与版本契约。
- 列表默认隐藏已归档租户，并提供状态筛选与恢复入口。

**Non-Goals:**
- 不做物理删除、不做数据导出/清理、不做租户空间目录删除。
- 不新增租户生命周期状态机（不引入 provisioning/archived 之外的第二套状态字段）。
- 不改动租户编辑器五标签结构、成员/角色/授权规则、租户 `code` 与稳定 ID。
- 不改变停用（`active=false`）既有语义与管理员连续性守卫。

## Decisions

### D1. 归档 = `archived_at` 非空 + `active=false`，复用既有失效门
新增可空列 `tenants.archived_at`（NULL=未归档），归档时同时置 `active=false`。
- 理由：所有「租户是否可用」的判定当前都读 `active`，将归档与停用叠加即可让登录、租户选择、业务请求、私有数据目录、待办等现有门自动拒绝归档租户，无需逐个改调用点，风险最小。
- 归档在同一事务内遵守既有 `member-tenant-assignment`「有效租户归属在所有写入口保持连续」约束：统计该租户全部启用成员的 user_id，置 `active=0` 后调用既有 `_check_all_affected_active_tenant_continuity`；若会使任一启用账号失去全部有效租户则拒绝整笔归档。这与既有「停用租户」行为一致，不为归档开例外。
- 备选 A：只加 `archived_at`，逐一改造所有 `active` 判定点。否决——改动面大、易漏，回归风险高。
- 备选 B：直接物理删除租户行及其依赖行。否决——破坏跨表引用与审计链，不可恢复，且违反 `tenant-resource-isolation` 保留边界。

### D2. 归档/恢复在同一身份事务写审计并各递增一次版本
`archive_tenant` 与 `restore_tenant` 都在单个 `_tx()` 内：校验授权/近期密码/版本 → 改 `tenants` 行 → 写 `tenant.archive` / `tenant.restore` 审计 → 提交。失败整体回滚返回 503，与既有租户写入一致；版本各只 +1 一次。
- 理由：与 `tenant-management`「租户写入使用版本与同库审计」既有契约保持一致，避免部分提交与审计缺口。

### D3. 恢复一步到位：清 `archived_at` + `active=true` + 管理员连续性校验
恢复操作在同一事务内清 `archived_at`、置 `active=true`，并在提交前复用既有「恢复停用租户需至少一个 active 用户的 active `tenant_admin`」守卫。
- 理由：一步恢复符合「归档可恢复」的用户预期；直接回到可用态时必须过与停用恢复相同的安全门，不能借归档绕过管理员连续性。
- 备选：恢复只清 `archived_at` 并保持 `active=false`，再由管理员单独启用。否决——需要两次操作且中间态语义含糊。

### D4. 归档守卫：平台管理员 + 近期密码 + 版本 + 对象保护
归档要求 `_require_management_write()`、有效平台管理员、近期密码、`expected_version`；服务端 MUST NOT 归档部署默认租户（`code='default'`，由 `common/startup_hooks.py` bootstrap 固定创建）或操作者当前请求上下文所属租户（`ctx.tenant_id`）。归档可恢复且不丢数据，因此确认强度以「近期密码」为准，不再要求输入租户编码。
- 理由：归档会把租户从服务中移除，需要与「删除」匹配的防误操作强度；平台管理员的登录密码本身就是一次显式的、可验证的意图声明，与其它平台写操作的近期密码口径一致。默认租户与当前租户被归档会让平台自身失去可操作上下文。
- 备选 A：要求输入租户编码二次确认。曾作为初版设计，因操作成本高于收益被否决——密码已提供同等的防误操作强度，且租户名/编码就在同一行可见。
- 备选 B：仅浏览器 `confirm`。否决——防误操作不足，且与其它平台写操作的近期密码口径不一致。

### D5. HTTP 契约：`DELETE` 归档 + `POST operation='restore'` 恢复
在既有 `PlatformTenantHandler` 增加 `DELETE`（归档）与 `operation='restore'`（恢复），复用 `/api/platform/tenants/([^/]+)` 路由并在 `route_registry.py` 同一 `RouteEntry` 增加 `DELETE`。
- 理由：软删除用 `DELETE` 语义直观；恢复属于状态变更，与现有 `operation` 分派（`profile`/`name`/`status`）风格一致。
- 备选：归档也用 `POST operation='archive'`。否决——按钮语义是删除，`DELETE` 更贴合且便于前端统一。

### D6. 已归档租户只读，普通编辑/启用必须拒绝
`set_tenant_status` 与 `set_tenant_profile` 在目标已归档时 MUST 拒绝（要求先恢复）；已归档租户只能通过恢复操作改变状态或名称/启用位。归档不释放 `code` 唯一占用。
- 理由：防止出现 `active=true` 但 `archived_at` 非空的不一致态，也保证恢复路径唯一、可审计。

### D7. 前端复用 `openAdminModal`，列表加状态筛选
行内「删除」按钮（`admin-row-btn danger` + `fa-trash`）对未归档行渲染，对默认/当前租户隐藏；确认弹窗复用 `openAdminModal`，只保留既有 `recent_password`（密码、必填）一个字段，标题与副标题说明「删除=停用且可恢复」。已归档行渲染归档标记与「恢复」按钮。列表上方新增状态筛选（未归档/启用/停用/已归档），默认「未归档」保持现状。
- 理由：复用既有弹窗与字段渲染，最少新增 UI 代码；后端仍独立校验，前端隐藏不是授权手段。

## Risks / Trade-offs

- [出现 `active=true` + `archived_at` 非空的不一致态] → D6 拒绝已归档租户的普通编辑/启用；只有恢复操作能清 `archived_at`，且恢复会一并置 `active=true`。
- [归档后同一 code 无法复用] → 这是刻意的：归档不删数据，code 与稳定 ID 保留；需要复用请先恢复并改名（改 code 本就不允许）。文档与规范显式声明。
- [`code='default'` 未必总是存在或未必唯一代表默认租户] → 保护按 `code='default'` 判定，该 code 由 bootstrap 固定创建且不可修改；若无该租户则无此保护，不影响其它逻辑。
- [缓存静态资源导致旧前端看不到按钮] → 属正常前端发布行为；后端接口独立可用，不影响正确性。
- [归档对象保护把「当前租户」误判] → 仅当 `ctx.tenant_id` 非空且等于目标时拒绝；平台控制面无租户上下文时不影响。
- [成员只属于目标租户导致无法归档] → 这是既有归属连续性约束的刻意结果，与停用一致：平台管理员需先为受影响成员分配其他有效租户；错误返回 `last_active_tenant_required`，不静默放行。
- [归档/恢复并发] → 沿用 `expected_version`；过期版本返回 409 并保持已提交状态，前端重新加载。

## Migration Plan

- `auth/store.py` 新增 `_migration_12`：`ALTER TABLE tenants ADD COLUMN archived_at INTEGER`（可空）。既有行保持 `NULL`，即全部未归档，行为不变。
- 发布顺序：先迁移后端与接口，再发布前端。回滚只需移除新按钮/接口调用并保持列存在；不涉及数据删除，无需回填或恢复脚本。
- 归档是运行期操作，无一次性批量迁移；不需要 feature flag（纯新增接口与列，旧客户端不受影响）。

## Open Questions

无。
