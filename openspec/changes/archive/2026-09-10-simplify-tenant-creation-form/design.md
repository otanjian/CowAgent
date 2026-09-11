## Context

见 `proposal.md` Why/What。当前接入点：

- `auth/service.py` 的 `create_tenant()` 以 `admin_username` / `admin_display` / `admin_password` 为必填参数，在同一 `BEGIN IMMEDIATE` 事务内写入 `tenants`、`users`（`must_change_password=1`、`temp_password_expires_at=now+3d`）、`memberships`、`membership_roles`、两个内置 `roles`（`tenant_admin` / `member`）、`departments` 的 `__root__` 行和 `audit_events`；密码哈希在锁外计算。
- `channel/web/admin_handlers.py` 的 `PlatformTenantsHandler.POST` 直接把请求体的 `admin_username` / `admin_display` / `admin_password` 透传给服务，并回显 `tenant`。
- `channel/web/static/js/identity-admin.js` 的 `openTenantCreate()` 用通用 `openAdminModal()` 渲染 6 个字段；`openAdminModal()` / `submitAdminModal()` 目前只有 `submit` / `onConflictReload` 生命周期钩子，`submitAdminModal()` 在成功后调用 `closeAdminModalNoPrompt()`，因此**没有**「成功后接续打开另一个对话框」的钩子。
- `auth/http_policy.py` 对 `/api/platform/tenants` 已有 `platform` 策略，本次不改动。
- 系统当前没有创建平台账号或显式创建租户管理员的端点：`/api/platform/users` 只有 GET/PATCH，成员创建（`create_member`）需要调用者已是该租户的 tenant_admin。`set_tenant_admin` 只能绑定**已存在且 active** 的 User。

因此「租户创建与管理员创建解耦」会暴露一个前置缺口：新租户在绑定管理员之前无法自行创建账号。本设计不新增账号创建入口，只记录该缺口。

## Goals / Non-Goals

**Goals:**

- 创建租户只承载租户生命周期字段（`code` / `name` / 操作者 `recent_password`），不传输任何租户内账号或明文初始密码。
- 保持 `create_tenant` 单一写入事务与既有审计/目录派生/跨租户目录包含校验不变。
- 创建成功后让管理员配置成为可发现的下一步，而不是让新租户静默停在无管理员状态。
- 让已显式传参的调用方（CLI `bootstrap`、in-place `register`、绝大多数测试）行为完全不变。

**Non-Goals:**

- 不新增「创建平台账号」端点或页面；不在新租户内自动建号。
- 不改变 `set_tenant_admin` 的授权、近期密码、最后管理员保护与审计语义。
- 不改 `identity.db` 表结构，不引入 provisioning/archived/邀请状态机。
- 不改变停用/恢复租户对 `tenant_admin` 连续性的校验强度。
- 不为兼容旧前端保留 `admin_*` 请求字段的必填语义。

## Decisions

1. **`admin_*` 改为可选参数，而非移除参数或新增专用方法**
   `create_tenant(..., admin_username: Optional[str] = None, admin_display: str = "", admin_password: Optional[str] = None)`。三者中 `admin_username` 与 `admin_password` 同时非空即视为「显式指定初始管理员」，走现有建号分支与弱密码校验；否则整体跳过建号。
   备选一「直接删除三个参数、所有调用点改用 `set_tenant_admin`」：会让约 25 个测试文件与 `bootstrap` 的调用语义发生大规模机械改写，且 `bootstrap` 本质上就是「建租户 + 建首个管理员」，删除参数后要在别处复制一遍建号逻辑，风险更高。
   备选二「新增 `create_tenant_without_admin()`」：同一事务逻辑出现两份，长期易漂移。

2. **以字段是否提供作为分支开关，而不是新增布尔参数**
   显式布尔（如 `create_admin=True`）会引入「传了 admin_username 但 `create_admin=False`」的矛盾组合；用字段非空判断可保持单一真值来源。校验顺序为：先租户 code / 唯一性 / 目录派生与包含校验，再在确有管理员时校验弱密码——避免无理据的弱密码错误阻塞本不含密码的请求。

3. **前端复用 `set_tenant_admin`，不新建对话框**
   新增 `openAdminModal` 的 `afterSuccess` 回调，由 `submitAdminModal()` 在 `closeAdminModalNoPrompt()` **之后**调用（顺序不可颠倒，否则接续对话框会被立即关闭，`_adminModal.dirty` 也会被新对话框覆盖）。回调里携带新建租户的 id 调用现有 `openTenantAdmin(id)`。
   备选「把管理员字段留在创建表单但改为选人」：与用户目标（去掉管理员维护）相反；备选「创建成功只提示、由管理员自己去列表点按钮」：用户明确选择自动引导。

4. **租户仍以 `active=true` 创建**
   保持 `create_tenant` 的返回契约与「启用状态变更保护租户边界」的恢复语义。允许 `active=true` 且无有效 `tenant_admin` 的窗口期，并把该窗口期显式写进 delta spec；不改为 `active=0`，因为那会让创建操作的返回语义与既有的 `active` 语义、以及依赖「新建即可用」的其他 change 产生歧义，且需要额外的启用步骤。

5. **后端忽略未知请求字段，不做严格拒绝**
   现有 handler 逐字段 `data.get(...)`，未使用严格 schema；本次延续该风格，旧前端残留的 `admin_*` 字段被忽略而不是 400。改动只发生在本次发布的前端与后端同步更新范围内。

6. **i18n 文案按「可能残留」处理**
   `admin_field_admin_username` / `admin_field_admin_password` 在三套语言字典中仍被 `openTenantAdmin`（显示名）与成员表单复用，删除会误伤其他视图；因此保留字典条目，只从创建表单的字段列表移除。不再新增文案键。

## Risks / Trade-offs

- [新租户在绑定管理员前无法自行创建账号（无平台建号入口）] → 本次把「创建成功后自动引导绑定已有账号」作为必做项；同时把「新建账号入口」记为后续 change 缺口，不假装已具备该能力。
- [`afterSuccess` 回调在对话框关闭后触发，若实现顺序错误会吞掉接续对话框] → 在任务中显式规定调用顺序，并补一条前端用例断言回调在关闭后执行、接续对话框可见。
- [旧前端（缓存资源）仍提交 `admin_*` 却被忽略，造成「填了却不生效」的困惑] → 后端不报错但也不建立管理员，属可接受降级；发布说明需提示刷新静态资源。
- [初始 `active=true` 且无管理员与「恢复租户需有有效管理员」形成不对称] → 有意为之并写入 spec 场景；恢复路径的校验不因本次改动放宽。
- [移除弱密码校验的触发路径后，可能掩盖其他调用方的密码校验依赖] → `_COMMON_PASSWORDS` / `MIN_PASSWORD_LENGTH` 校验仅在提供密码时执行，保留给 `bootstrap`、`register` 与 `set_tenant_admin` 的等价路径；测试中保留一条「显式提供弱密码仍被拒」的回归用例。

## Migration Plan

无数据迁移：表结构不变，已有租户与其管理员不受影响。回滚只需回退代码；期间创建的「无管理员租户」在回滚后仍可通过租户列表的「管理员」按钮补齐，不产生不可恢复状态。部署顺序为后端与静态资源同批发布。

## Open Questions

无。管理员来源、初始启用状态、输入方式与 `recent_password` 取舍均已确认。
