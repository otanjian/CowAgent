# 成员 ↔ 租户多租户分配方案

日期：2026-09-09。范围：Web 管理控制台的「成员管理」视图，让管理员在新建/编辑成员时选择目标租户（多选）。

## 1. 目标与约束

1. **新建成员**：选择目标租户（可多选），可选范围 = 操作者持有 `tenant_admin` 资格的租户。
2. **编辑成员**：同样逻辑，与「编辑资料」解耦。
3. **平台管理员同样受限**：可选租户也仅限其持有 `tenant_admin` 资格的租户（对齐 `account-administration` 规范「平台管理员没有目标租户资格时不得经租户接口管理身份」）。不做「平台管理员看全部租户」——那属于独立平台能力，需另立 OpenSpec change。

## 2. 关键结论：后端几乎零新增

- `create_member`（`auth/service.py:2334`）已支持 `create-new` / `bind-existing` 两种操作。
- `update_member`（`auth/service.py:2432`）已含连续性保护：`_check_admin_continuity`（最后租户管理员）、`_require_active_tenant_for_enabled_user`（停用最后有效归属时拒绝）。
- 每租户授权已由 HTTP 层 `_require_context(require_tenant=True)` + `_require_tenant_admin(ctx)` 逐租户校验（`admin_handlers.py:48`）。只要前端按目标租户覆盖 `X-Tenant-ID`，跨租户越权天然被拒。

因此本需求不需要新增权限/连续性代码，核心是前端逻辑。

## 3. 数据流

### 3.1 租户候选集

复用 `/auth/me`（`self_context`，`auth/service.py:1309`），它已返回 `tenants[].membership.roles[].code`。前端过滤 `roles` 含 `tenant_admin` 的租户作为候选集。平台管理员走同一过滤。

### 3.2 新建成员（多租户）

前端在弹窗中展示租户多选（默认当前租户）；每个选中租户各配角色（默认 `member`）+ 可选部门。提交时按选中租户循环调用既有端点：

- 新建账号：首个租户 `operation=create-new`（携带 `temporary_password`），其余租户 `operation=bind-existing`（不携带密码）。
- 绑定已有账号：全部租户 `operation=bind-existing`。

每个请求的 `X-Tenant-ID` 覆盖为对应目标租户，服务端逐租户校验 `ctx.is_tenant_admin`。

### 3.3 编辑成员（解耦为两个动作）

- **编辑资料/角色/部门**：维持当前租户视角，复用 `update_member`（不变）。
- **调整所属租户**：多选展示操作者可管理的租户，勾选 = 归属；增 = `bind-existing`，删 = `update_member(active=false)`（受连续性保护）。操作者只能看到/管理其 `tenant_admin` 租户内的成员关系，不泄露其他租户。

## 4. 改动清单

### 4.1 前端（`channel/web/static/js/identity-admin.js` + i18n）

1. 新增 `_adminTenants`：缓存 `/auth/me` 中 `roles` 含 `tenant_admin` 的租户。
2. `apiFetch` 支持按调用覆盖 `X-Tenant-ID`（新增可选 `tenantId` 参数）。
3. `openMemberCreate`：新增「目标租户」多选 + 每租户角色/部门；提交逻辑按 3.2 循环。
4. `openMemberEdit`：拆出「调整所属租户」入口；实现增删租户的多选逻辑。

### 4.2 后端

- 可选读辅助：`GET /api/identity/administered-tenants`（返回操作者可管理的租户，含对某目标用户的成员状态），用于编辑视图渲染。若先不做，可用「每租户 `GET /api/tenant/members?q=<username>`」复用替代（N 次调用）。
- 创建/绑定/删除全部复用既有单租户端点，无新写权限代码。

### 4.3 测试

- 前端契约：租户候选过滤、多租户创建操作序列（首租户 create-new / 其余 bind-existing）、编辑增删租户。
- 后端：跨租户越权 403（A 管理员写 B 租户，复用现有 ctx 校验）；多租户绑定已有账号；删除最后归属/最后管理员的连续性拒绝。

## 5. 安全边界

- 可选租户 = 操作者具 `tenant_admin` 资格的租户，跨租户提权从源头杜绝。
- 编辑「调整所属租户」仅暴露操作者可管理租户内的关系，不泄露其他租户成员关系。
- 删除租户归属走既有连续性约束（`member-tenant-assignment`）。
