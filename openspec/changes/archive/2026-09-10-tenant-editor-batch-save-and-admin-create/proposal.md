## Why

租户编辑器（`tenant-tabbed-editor` 已归档）当前有两个可用性问题，且都已被归档规范固化为契约，不能靠前端小修绕过。

**一、按标签独立保存造成重复操作，且当前密码字段常驻表单。**

四个标签各自持有保存按钮，每个面板内都渲染一个「当前密码确认」输入框。修改跨标签时常需要多次点击保存、反复输入同一密码；密码框长期留在表单里，既不必要地延长了密码在界面中的停留时间，也让「哪些改动会被提交」不可见。更关键的是，这些写请求分属不同端点、各自使用租户版本做 `expected_version`，缺少统一的提交顺序与版本串联契约，任何「一次保存多个标签」的实现都必须先把这个顺序与失败语义定下来，否则会出现版本冲突或部分提交后被误报为整体成功。

**二、「租户管理」标签无法新建管理员账号，且规范显式禁止。**

归档后的 `租户管理员配置入口收在租户管理标签` 明确写着「MUST NOT 在此处新建账号或生成临时密码」，只允许绑定已有账号。而 `平台直接配置管理员且不接管账号` 又写着「可绑定已有有效 User 或明确创建新账号」。两条需求互相冲突：平台管理员在只部署了一个平台账号、或需要为一个新租户开一个全新管理员时，没有可用的建号入口。

**三、附带发现：选择已有账号时显示名实际改不动。**

`IdentityService.set_tenant_admin` 只在**新建**成员关系时写入 `display_name`；当目标账号已是该租户的有效成员时，传入的 `display_name` 被静默忽略。这使产品期望的「选完账号后仍可修改显示名」在当前实现下无法生效，不是前端问题。

## What Changes

- **统一保存**：`channel/web/static/js/identity-admin.js` 的租户编辑器改为单一保存动作，提交当前所有已改动标签；按「租户管理员配置 → 资源授权 → 基本信息」固定顺序串行提交，每一步使用上一步回显的版本，失败即停并指明失败步骤。
- **密码弹窗**：移除基本信息与租户管理面板中的常驻 `recent_password` 输入框；改由保存时弹出的密码提示收集，校验失败可原地重试并保留全部草稿，成功后不保留密码。
- **新建租户管理员账号**：`租户管理` 标签新增「选择已有账号 / 明确创建新账号」模式切换；新建模式提交用户名、显示名与初始密码。
- **新增服务方法**：`auth/service.py` 新增在单一事务内「建立全局账号 + 建立管理员成员关系 + 绑定 `tenant_admin` + 审计」的路径，并把用户名与临时密码校验从 `create_member` 提取为共享校验函数供两者复用。
- **修正显示名语义**：`set_tenant_admin` 在目标账号已是有效成员时，如提交了不同的显示名，则更新 `memberships.display_name` 并递增该成员版本；不改全局显示名。
- **扩展端点**：`POST /api/platform/tenants/{tenant_id}/admins` 增加显式 `mode`（`existing` / `new`），缺省为 `existing` 以保持向后兼容。
- **修改三条归档需求**：放开 `租户管理员配置入口收在租户管理标签` 的建号禁令并定义「选择已有 / 明确新建」双模式；在 `平台直接配置管理员且不接管账号` 中补齐新建账号契约与成员显示名可改语义；在 `租户写入使用版本与同库审计` 中补入统一保存的提交顺序、版本串联、失败即停与密码统一收集契约。
- **废弃在途 change**：`tenant-admin-account-picker` 已移入 `openspec/changes/archive/2026-09-10-tenant-admin-account-picker-SUPERSEDED`。它要改的 `openTenantAdmin()` 在 `tenant-tabbed-editor` 中已被删除，其界面契约（从有效账号中选择、未选中阻止提交、候选加载失败保留对话框）也已随该 change 归档进入主规范，已无实现对象。
- **修复验收期发现的缺陷：创建账号失败原因被隐去**。验收「创建新账号」时，界面只报「失败步骤：租户管理」而不说明原因。根因是前端仅对 409 作解释，服务端返回的 `weak_password` / `invalid_username` 被折叠；初始密码字段也缺少既有入口同款的强度提示。改为按服务端错误码给出可操作说明，并复用 `admin_field_password_hint`。
- **修复验收期发现的缺陷：强制改密门禁不可见**。新建的管理员账号带 `must_change_password`，首次登录本应进入改密门禁，但 `#account-password-modal` 位于 `#app` 之内，而门禁流程恰好隐藏 `#app`，导致弹窗被折叠成 0×0：登录成功后密码被清空、界面停在登录页、无任何提示，表现为「点登录没有反应」。改为把该弹窗放到 body 层（与 `#login-overlay` 同级），使「隐藏业务界面」与「门禁可见」同时成立。此为既有实现违反 `self-password-flow` 已有需求，不新建能力。

### 与 PRD 的关系

本 change **不新建规范能力**，只修改既有 `tenant-management` 与 `self-password-flow` 规范并调整既有产品代码，属既有能力的行为修正与补全。

- 需求基线按 `openspec/config.yaml` 指向的 PRD-01～12 v1.2。但仓库所称的 PRD 原文当前仍缺失（沿袭 `tenant-tabbed-editor` 与已废弃 picker change 的记录），因此本次行为基线取自 `openspec/specs/tenant-management/`。PRD 原文恢复后 MUST 复核两处口径：①新建管理员账号的临时密码究竟由操作者提交还是由系统生成后一次性展示；②一次保存多标签是否 PRD 有既定提交顺序要求。
- **数据唯一归属**：账号、成员关系、角色绑定与审计全部归属 `identity.db`，由 `IdentityService` 在单事务内写入。租户的 `shared_root` 与目录归属不变，本 change MUST NOT 触碰租户目录。
- **跨 change 依赖**：依赖已归档的 `tenant-tabbed-editor`（标签骨架、资源授权按类别维护、租户空间只读投影、管理员入口收在标签内）。本 change 不依赖 `tenant-admin-account-picker`，该 change 已废弃。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `tenant-management`：修改 `平台直接配置管理员且不接管账号`（新增账号创建契约与成员显示名可改）、`租户写入使用版本与同库审计`（统一保存的顺序、版本串联、失败语义与密码收集契约）、`租户管理员配置入口收在租户管理标签`（放开建号禁令，定义两种模式）。

## Impact

- **代码**
  - `auth/service.py`：新增建号+绑定的单事务方法；`set_tenant_admin` 补齐成员显示名更新；提取用户名/临时密码共享校验。
  - `channel/web/admin_handlers.py`：`PlatformTenantAdminsHandler.POST` 增加 `mode` 分派与参数校验。
  - `channel/web/static/js/identity-admin.js`：统一保存、密码弹窗、移除常驻密码字段、模式切换与新建账号表单。
  - `channel/web/static/css/console.css`：密码弹窗与模式切换的样式。
  - `channel/web/static/js/console.js`：三套语言补充密码弹窗、模式切换、新建账号字段、多标签保存结果与失败步骤文案。
- **API**：`POST /api/platform/tenants/{id}/admins` 新增可选 `mode` 与 `username` / `temporary_password` 字段，缺省行为不变（向后兼容）。不新增端点。
- **权限**：不扩大可见面。建号与绑定同属平台管理员 + 近期密码校验范围；租户成员接口仍只对 `tenant_admin` 开放，本 change MUST NOT 放宽 `create_member` 的操作者范围。
- **数据**：`identity.db` 表结构不变，不新增列、不新增迁移。新账号复用既有 `users` / `memberships` / `membership_roles` 写入路径与 `must_change_password` 字段。
- **安全**：从表单移除常驻密码字段缩短密码驻留时间；密码 MUST NOT 落盘、MUST NOT 回显、MUST NOT 进入审计或日志。新建账号强制首次改密，避免长期共享口令。
- **测试**：需覆盖统一保存的顺序与版本串联、失败即停与部分成功、密码弹窗取消/失败重试/成功不保留、建号成功与冲突回滚、成员显示名更新与「不改全局显示名」、以及既有按标签保存用例的迁移。
- **feature flag**：不引入运行开关，能力随代码发布生效；无新增环境变量。
