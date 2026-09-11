## Why

`simplify-tenant-creation-form` 已把租户创建与管理员的账号创建解耦，创建成功后自动接续打开「配置租户管理员」对话框并把「绑定已有有效账号」定为唯一管理员配置路径。但该对话框目前用一个自由文本输入框收集目标账号，只提示「已有有效账号的用户 ID」，要求平台管理员手工填写形如 `usr_zV2k24R8SVRqX8fr` 的内部主键。运行中该输入极易被填成租户编码或登录名，服务端按 `WHERE id=?` 查询不到后统一返回 404 `not found`（`auth/service.py` 的 `set_tenant_admin`），而界面把这个 404 渲染成 `user not found or disabled`，使用者无法从提示中判断是「ID 写错」「账号不存在」还是「账号被停用」。上一 change 把这条路径设为创建租户后的必经步骤，却未提供可发现的账号选择方式，使新租户的管理员配置实际上不可用。

## What Changes

- 在 `channel/web/static/js/identity-admin.js` 的通用对话框字段渲染器中新增 `userpicker` 字段类型：`fieldHtml()` 增加分支，新增 `_initUserPicker(node)` 异步初始化，复用既有 `_initResourceGroup` / `_initModelDefaults` 的加载模式。
- 把 `openTenantAdmin()` 的 `user_id` 文本字段改为 `userpicker`，候选集取自既有 `GET /api/platform/users?status=active`，展示「显示名 · 用户名」，提交值为稳定 User ID；账号大于一页时由内置搜索框以既有 `q` 参数在服务端过滤。
- 候选集为**全局 active 账号**，不过滤已是本租户或其他租户成员的账号：`set_tenant_admin` 对既有有效成员等价于授予 `tenant_admin` 角色，属于合法操作。
- 选择器加载失败或未选出账号时 SHALL 阻止提交并显示错误，MUST NOT 静默降级回自由文本输入。
- 不新增后端端点、不改 `auth/http_policy.py`、不改 `set_tenant_admin` 的服务端校验与审计语义；`display_name` 字段保持自由文本（写入 membership 显示名，非账号属性）。

## Capabilities

### New Capabilities

无。本次不引入新能力，只修改既有 `tenant-management` 的管理员配置交互契约。

### Modified Capabilities

- `tenant-management`: 在「平台直接配置管理员且不接管账号」中补充「目标账号 SHALL 从当前有效账号中选择，MUST NOT 要求操作者输入内部 User ID」的界面契约，以及候选集范围、候选不可用时的失败语义。

## Impact

- **代码**：`channel/web/static/js/identity-admin.js`（`fieldHtml()`、新增 `_initUserPicker()`、`openTenantAdmin()` 字段定义）、`channel/web/static/js/console.js`（三套语言下补充选择器加载/空态/搜索/超页提示文案键）。
- **API**：无新增或修改。复用 `GET /api/platform/users`（仅 GET/PATCH，平台管理员可见）与 `POST /api/platform/tenants/{id}/admins`。
- **权限**：选择器与被它服务的对话框同属平台管理员可见范围，不扩大可见面；非平台管理员在打开对话框前已被拒绝。
- **数据**：`identity.db` 表结构不变，不新增查询字段；候选集只读取 `users` 的 `id` / `username` / `display_name` / `active`。
- **依赖**：依赖 `simplify-tenant-creation-form` 已交付的 `afterSuccess` 接续流程与 `set_tenant_admin`；依赖 `tenant-management` 既有「配置管理员需近期密码验证」与版本/审计契约，不新增权限与角色。
- **已知范围限制（不在本次闭合）**：候选集仍需至少一个全局 active 账号存在；系统至今没有「平台新建账号」入口，全新部署若只保留唯一平台管理员，仍需用该管理员账号完成首租户绑定。该缺口沿用上一 change 的记录，转交后续 change。
- **PRD 映射缺口**：仓库所称 PRD-01～12 v1.2 原文仍缺失，行为基线取自 `openspec/specs/tenant-management/`；PRD 恢复后需核对管理员配置是否另有指定账号来源要求。
