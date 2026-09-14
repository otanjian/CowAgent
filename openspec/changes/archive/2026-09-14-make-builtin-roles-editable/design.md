## Context

内置角色 `member`/`tenant_admin` 由 `auth/policy.py` 的固定常量定义，建租户时 `IdentityService` 把角色行写入 `roles` 表（`builtin=1`，含 `permissions_json`），但**有效权限解析**与**持久化值**长期分离：

- `IdentityService._permissions_for_membership()`（`auth/service.py`）把 `member`/`tenant_admin` 排除在 `role_permissions` 映射之外，只把编码交给 `permissions_for_roles()`。
- `permissions_for_roles()`（`auth/policy.py`）对两个内置编码直接返回 `default_permissions_for()` 的固定常量（`MEMBER_DEFAULT_PERMISSIONS` 七项、`TENANT_ADMIN_DEFAULT_PERMISSIONS` 九项）。
- 建租户种子（`auth/service.py` 的租户默认构造）却把 `tenant_admin` 写成 `list(PERMISSION_CATALOG)`、`member` 写成硬编码七项，控制台 `list_roles` 读的正是持久化值。

因此「控制台显示」来自持久化值、「接口判定」来自固定常量，二者已经分叉。编辑内置角色在本 change 之前被 `update_role`/`delete_role` 的 `if row["builtin"]` 守卫彻底禁止。

## Goals / Non-Goals

**Goals**
- 让 `member` 能按 3.1 正常使用平台（对话、智能体、技能、工具、模型、个人读数），并可按 3.2 创建自有资源。
- 让 `tenant_admin` 的权限集合与职责（3.1.3 的 2.1～2.3）一致，且控制台显示与接口判定同源。
- 允许租户按需编辑内置角色，同时保留管理员连续性与「不可删除内置角色」的保护。

**Non-Goals**
- 不引入可编辑的作用域枚举、部门数据范围或多部门任职。
- 不改变身份管理资格的派生方式（仍由 `tenant_admin` 角色编码决定）。
- 不把平台内置角色 `platform_admin` 变成租户可分配/可编辑对象。
- 不改动资源授权表结构或 `member`/`tenant_admin` 之外的角色语义。

## Decisions

### 决策 1：内置默认集合改为显式枚举，并作为「回落值」而非唯一来源

`MEMBER_DEFAULT_PERMISSIONS` / `TENANT_ADMIN_DEFAULT_PERMISSIONS` 改为本 change 明确枚举的集合（成员 18 项、管理员 23 项，管理员为成员超集）。仍使用显式元组而不是 `PERMISSION_CATALOG` 动态展开，保留「目录扩充不自动扩权」不变式：新增目录项必须显式写入常量才进入内置集合。

`TENANT_ADMIN_DEFAULT_PERMISSIONS` 虽在本次等于全部已登记目录项，仍以显式 id 列出，未来新增权限不会自动落到管理员。

### 决策 2：内置角色有效权限以持久化 `permissions_json` 为准

`_permissions_for_membership()` 不再把内置编码排除，而是对**所有**角色读取 `permissions_json` 填入 `role_permissions`；`permissions_for_roles()` 改为：

1. 编码在 `role_permissions` 中 → 取持久化集合（即使是空集，也表示管理员显式清空）；
2. 否则（历史/未持久化数据）→ 内置编码回落到显式默认集合，自定义编码回落为空集。

这样控制台显示与接口判定同源，且编辑保存后立即对下一次请求生效（权限每请求重新解析）。空集语义与「授权空集合必须拒绝」一致：清空即无业务权限。

### 决策 3：允许编辑内置角色，禁止删除；身份资格与编码绑定

`update_role()` 去掉 `row["builtin"]` 的硬拒绝，允许修改姓名、功能权限、资源授权、默认模型（编码本就不可通过该接口修改，作用域不变）。`delete_role()` 继续拒绝内置角色；`create_role()` 继续拒绝与内置编码重名。

身份管理资格 `is_admin_role()` / `_is_tenant_admin()` 只看角色编码，编辑权限不改变谁拥有该资格，也不改变管理员连续性判定。为防控制台自锁，新增最小读取守卫：保存 `tenant_admin` 时要求其集合仍包含 `tenant.info.read`、`tenant.members.read`、`tenant.org.read`；否则整笔拒绝（409）。`member` 无此守卫。

### 决策 4：版本化迁移回填，安全且幂等

新增 `auth/store.py` 迁移：对 `roles` 表中 `builtin=1 AND code IN ('member','tenant_admin')` 的行，把 `permissions_json` 置为对应显式默认集合并 `version = version + 1`。理由：

- 编辑能力在本 change 之前不可用，持久化值只可能来自旧种子，不存在需要保留的用户自定义；
- 回填后 `member` 获得新增使用权限、`tenant_admin` 对齐全目录，且与新建租户种子一致；
- 迁移按 schema 版本仅执行一次，天然幂等；不改表结构、不跨租户。

新建租户的种子改为调用 `default_permissions_for()`，与常量保持单一来源。

### 决策 5：前端仅新增编辑入口

`loadRolesView()` 对内置角色同时渲染「编辑」与「复制」，仍不渲染「删除」；既有角色编辑器 `openRoleEdit()` 已支持 `mode='edit'`、编码锁定与 `expected_version`，无需新编辑器。内置角色行保留「内置」标记，避免误认为自定义角色。

## 风险与缓解

- **自锁风险**：管理员移除 `tenant_admin` 的身份读取权限后无法在控制台看到成员/角色页。缓解：最小读取守卫；平台管理员仍可通过平台目标租户入口修复。
- **回填覆盖**：理论上直接改库的自定义会被覆盖。缓解：仅回填 `builtin=1` 行，两编码固定；迁移记录版本，可审计。
- **前端缓存**：角色保存后 `loadRolesView()` 重新拉取，`/auth/context` 每请求重解析，不缓存权限。

## Migration

1. 新增迁移回填内置角色 `permissions_json` 与 `version`。
2. 部署后新租户直接以新默认集合建角色。
3. 回滚：迁移只改两列数据，回退代码到旧常量即可恢复旧判定；旧持久化值可由备份恢复（迁移前备份 `identity.db`）。

## Open Questions

- 无。内置默认集合取值经用户确认（成员「使用型 + 可创建自有资源」、管理员「全部目录」、编辑范围「名称 + 权限 + 资源授权 + 默认模型」）。
