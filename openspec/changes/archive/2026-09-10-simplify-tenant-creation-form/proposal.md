## Why

「系统设置 → 租户 → 创建租户」当前要求平台管理员在同一表单内填写新租户的首个管理员账号、显示名和初始密码。这与 `user-membership` 已确立的「账号与成员分离、管理员直接绑定已有有效账号」模型重复：租户创建是租户生命周期操作，账号口令属于账号生命周期，二者耦合在一个提交里既扩大了创建表单的敏感字段面（明文初始密码经由租户创建请求传输），也让创建流程必须内建一套临时密码与首次改密策略。租户管理的其余入口（`set_tenant_admin`）已经支持「绑定已有有效 User 并授予 tenant_admin」，因此创建租户只需产出可用的租户骨架，管理员配置改为紧随其后的独立、可审计操作。

## What Changes

- 创建租户表单只保留 `code`、`name` 与操作者本人的 `recent_password` 二次授权；移除 `admin_username`、`admin_display`、`admin_password`。
- `create_tenant` 不再强制建立管理员账号：`admin_*` 参数改为可选，全部缺省时只建立租户行、两个内置角色（`tenant_admin` / `member`）、虚拟组织根与成功审计；显式传入时保留现有行为，供 CLI `bootstrap` 与既有调用点使用。
- 创建成功后前端自动接续打开「配置租户管理员」对话框，复用现有 `set_tenant_admin` 流程绑定已有有效账号（不新建账号、不生成临时密码）。
- 创建的租户仍然 `active=true`，但允许在绑定首个管理员之前短暂没有有效 `tenant_admin`；恢复停用租户时的「至少一个有效 tenant_admin」校验保持不变。
- 租户创建不再回显任何临时密码。
- **BREAKING（仅限 `create_tenant` 的直连调用方）**：未提供 `admin_username`/`admin_password` 时不再创建管理员；以「创建后必然存在管理员」为前提的调用方需要显式传入 `admin_*` 或随后调用 `set_tenant_admin`。

## Capabilities

### New Capabilities

无。本次不引入新能力，只修改既有 `tenant-management` 的租户创建契约。

### Modified Capabilities

- `tenant-management`: 修改「创建租户一次建立可用身份关系」，把创建时强制的「有效管理员成员」改为「租户骨架 + 可选的初始管理员」，并明确管理员配置由随后的独立绑定操作完成、初始 `active=true` 允许暂无有效管理员、创建响应不回显临时密码。相应调整「正常创建租户」与「创建响应丢失后查询结果」场景。

## Impact

- **代码**：`auth/service.py`（`create_tenant` 签名与事务体）、`channel/web/admin_handlers.py`（`PlatformTenantsHandler.POST` 请求字段）、`channel/web/static/js/identity-admin.js`（`openTenantCreate` 字段与成功后接续）、`channel/web/static/js/console.js`（i18n 文案在三套语言下的取舍）。
- **API**：`POST /api/platform/tenants` 请求体去掉 `admin_username` / `admin_display` / `admin_password`；未知字段被忽略，不新增端点、不改 `auth/http_policy.py` 的 `platform` 策略。
- **数据**：`identity.db` 表结构不变；新建租户在未绑定管理员时没有对应 `users` / `memberships` / `membership_roles` 行。
- **兼容**：`bootstrap()` 与 in-place `register` 仍显式传入 `admin_*`，行为不变；多数测试调用点显式传参，无需修改。
- **已知范围限制**：系统当前没有「新建平台账号」入口（`/api/platform/users` 仅 GET/PATCH），新账号只能由 `create_member`（需 tenant_admin）或带 `admin_*` 的 `create_tenant` 创建。因此新租户在绑定首个管理员前无法自行创建账号；本次不新增账号创建入口，记录为后续 change 的缺口。
- **PRD 映射缺口**：仓库所称 PRD-01～12 v1.2 原文仍缺失，本次行为基线取自 `openspec/specs/tenant-management/` 与 `user-membership/` 既有规范；PRD 恢复后需核对租户创建是否另有强制「初始管理员」要求。
- **依赖**：依赖 `set_tenant_admin`（`tenant-management` 已验收的「平台直接配置管理员且不接管账号」）作为唯一管理员配置路径，不新增权限与角色。
