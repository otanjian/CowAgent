# 角色资源授权——交付、迁移与回退说明

## 2026-09-15 方案变更（已复核，2026-09-16）

本文是原角色资源授权的交付记录。新方案映射旧个人菜单到正式页面，开放普通用户控制台并按所有权收窄对象范围；公共维护仍要求管理资格。原迁移和测试结论不证明新菜单映射、用户默认或统一功能已通过，新旧授权兼容按新 change 执行。

最新方案：[统一控制台与数据范围方案](unified-console-access-plan.md)；实施契约：[unify-console-by-data-scope](../../openspec/changes/unify-console-by-data-scope/proposal.md)。

**实际状态**：本文 §6.1 的组件集成结论仍然成立（两张 grant 表、平台 all、实际调用点守卫、迁移/回退守卫）；
§6.2 的三项延期项在本次复核中**仍为未交付**（未发现 `resource_catalog`、`model_policies`、`authorization_revision` 或
`AuthorizationService` 的实现）。本 change 在其上新增的不是新授权模型，而是：菜单 grant 的幂等迁移（`_migration_26`）、
用户默认与租户默认的分权（4.4/4.5）以及**按 owner/scope 的对象范围**（`auth/object_scope.py`）。
逐项判定见 [`evidence/8-5-doc-closure.md`](../../openspec/changes/unify-console-by-data-scope/evidence/8-5-doc-closure.md) §2。

---


日期：2026-09-09。对应 OpenSpec change：`add-role-resource-authorization`（任务 6.1/6.2/6.4）。
本文是**交付/运维层面**的说明，回答「如何启用、如何迁移存量授权、如何回退、哪些是组件集成、哪些尚未开放线上运行」。实现代码与规范化约定以 `openspec/changes/add-role-resource-authorization/` 与主规范 `openspec/specs/` 为准，本文只作操作与证据索引，不重复规范条款。

---

## 1. 组件集成 与 线上运行开放的差别（重要）

本 change **只接入并守卫已存在的消费组件**，**不开启任何新的线上运行入口**。二者必须区分：

| 维度 | 组件集成（本次完成） | 线上运行开放（延期，另立 change） |
| --- | --- | --- |
| 覆盖范围 | 现有技能/工具/Agent 装配、模型解析、角色管理、菜单/页签投影 | database 的 Web 聊天、流/轮询/取消、消息通道、定时任务执行 |
| 证据方式 | 真实身份库 + 受控工具/模型替身的**单元/集成测试** | 真实线上 Web 链路端到端验收 |
| 鉴权 | 现有组件在**实际调用点**重验当前授权 | 尚未开放的入口保持**服务端拒绝** |
| 结论 | 守卫已接入，接口占位/模拟实现**不算**生产门槛 | 不冒充已开放；凭据/审批/配额/隔离要求后续仍适用 |

> 因此，浏览器端看到「功能尚未开放」或「加载失败: forbidden」是**正确行为**——数据库消费者尚未开放，授权守卫拒绝是预期结果，不代表功能缺失。

---

## 2. 数据模型与迁移说明

### 2.1 新增存储（schema 迁移 vX）

- `role_resource_grants`：`(id, role_id, resource_kind, resource_id, action)`，表达「角色 → 具体资源的动作」。
- `tenant_resource_limits`：租户全局可分配资源上限。
- `roles.model_defaults_json`：角色每个已接通能力最多一个可选默认模型引用（属 `model.use` 集合，不自行赋权）。

### 2.2 迁移策略

- 复用 `schema_migrations`（幂等、版本化），不引入独立 `authorization_revision` 或 `staged/enforced` 状态机。
- 单次事务原子应用；导入失败整卷回滚，不留部分 DDL。
- **升级前守卫**：必须存在至少一个有效（已完成改密）平台管理员，否则拒绝启动（见 `tests/test_identity_migration_drill.py`）。

### 2.3 迁移中的授权映射（6.1 盘点结论）

- **已开放读取**：可按明确资源清单迁成显式 grants（例如已有 `skill.read` 权限租户，可迁移为具体技能 read）。
- **已关闭执行**：**不得**把从未开放的执行能力当成已授权。平台管理员由资格派生 all；普通角色新增执行权默认无。
- **技能维护拆分**：`agent.read` 不再授予技能编辑/启停（`skill.edit/enable` 为新动作），属有意收紧；管理员通过新角色显式分配。
- **不回退放宽**：授权迁移只做「明确资源 → grant」或「保留旧读取兼容只读角色」，绝不把 grant 表读取失败/缺记录解释为全部允许。

### 2.4 回退说明

- 回退只能在**仍执行授权守卫的兼容构建**上进行；不能删除 grant 表并把读取失败当作全放行。
- 回退后旧角色/持有者/凭据归属、资源内容与已有会话不因迁移搬家。
- 回退需重新满足「有效平台管理员 + 有效租户」约束，否则按守卫拒绝启动。

---

## 3. 脱敏证据（测试通过清单）

> 以下均为本地隔离库 / 隔离环境通过；不含真实凭据、密钥、用户敏感数据。数字为最后一次全量运行结果。

### 3.1 后端授权与迁移（pytest）

| 测试文件 | 覆盖 | 结果 |
| --- | --- | --- |
| `tests/test_identity_resource_authorization.py` | 平台 all、有限 grants、租户上限、多角色并集、空集合、未知/跨租户拒绝、目录投影 | ✅ |
| `tests/test_auth_preflight.py` | 只读预检工具：盘点角色/grants/默认值、目录投影、授权映射预览、严格只读 | ✅ 3 passed |
| `tests/test_identity_migration_drill.py` | 备份/中断/重试/恢复、遗留拒绝守卫 | ✅ 7 passed |
| `tests/test_http_policy.py` | HTTP 策略与 database 关闭门禁 | ✅ |
| 小计（授权+迁移+策略） | — | ✅ 46 passed |

含隔离身份库验证的任务 1.6、2.6、4.7、5.4 已覆盖于 `test_identity_resource_authorization.py` 与 `test_identity_migration_drill.py`。

### 3.2 前端逻辑（node:test）

| 测试文件 | 覆盖 | 结果 |
| --- | --- | --- |
| `tests/test_identity_admin_frontend.cjs` | 角色资源选择、模型默认、统一保存、未保存保护 | ✅ |
| `tests/test_sidebar_account_frontend.cjs` | 导航可达性、/auth/context 投影、改密门槛 | ✅ |
| `tests/test_scene_workbench_frontend.cjs` | 场景工作台前端 | ✅ |
| 小计 | — | ✅ 51 passed |

### 3.3 场景应用（pytest）

| 测试文件 | 覆盖 | 结果 |
| --- | --- | --- |
| `test_scene_activation.py`、`test_scene_skills.py`、`test_scenes_api.py`、`test_scene_workbench.py` | 场景激活/技能/API/工作台 | ✅ 37 passed |

### 3.4 浏览器端到端验证（任务 3.5）

> 已完成的浏览器验证场景（`cursor-ide-browser`）：
> 1. **模型默认落库**：编辑角色选 deepseek 默认 → `roles.model_defaults_json` 更新为 `{"chat":"provider:deepseek:..."}`。
> 2. **平台目标切换**：平台管理员切换目标租户 → 角色接口走 `/api/platform/tenants/<id>/roles`，目标隔离正确。
> 3. **成员访问未授权页被拒**：普通成员点「角色权限」→ 主内容区「加载失败: forbidden」，且权限集不含 role 管理权。

> 其余浏览器场景（未保存保护、跨页保存、全选快照、菜单直达拒绝、use 目录独立于管理菜单）已整理为下列**手工验收清单**，由用户自行测试。

---

## 4. 手工验收清单（交给验收人）

| # | 场景 | 操作 | 期望 |
| --- | --- | --- | --- |
| 1 | 未保存保护 | 编辑角色改动字段后点取消/切页 | 弹「有未保存的更改，确定离开？」；确认关闭、取消保留 |
| 2 | 跨页保存 | 多分页勾选技能+配置默认模型后保存 | 一次性提交或全部失败，无部分成功 |
| 3 | 全选快照 | 勾选「全选当前页」 | 保存具体 ID；新资源不自动加入 |
| 4 | 平台 all 展示 | 平台管理员打开角色 | 显示 all（含后续新增），只读，不需要手工勾满 |
| 5 | 菜单直达拒绝 | 普通成员直达管理 URL | 拒绝（不静默换作用域） |
| 6 | use 目录独立 | 只有使用权的成员 | 可取选择器最小信息，无需管理/配置读取权 |
| 7 | 版本冲突 | 两管理员并发编辑同一角色 | 一方 409，不覆盖 |
| 8 | 复制/成员关联 | 复制角色、关联成员 | 保留关联，无成员引用可删 |

---

## 5. 10 份规范映射

> 新规范（New）5 份，修改既有（Modified）5 份；对应主规范目录与 `openspec/specs/`。变更计数：25 新增 requirement、17 修改、1 移除，共 79 个场景（校验见 `validation.md`）。

| # | 规范 | 类型 | 主规范目录 | 核心内容 |
| --- | --- | --- | --- | --- |
| 1 | `platform-all-authorization` | NEW | `openspec/specs/platform-all-authorization/` | 有效平台身份派生 all、目标租户管理、资格撤销、不替代有效性/执行条件 |
| 2 | `role-resource-authorization` | NEW | `openspec/specs/role-resource-authorization/` | 五类资源明确标识分配、并集/上限、整体保存、迁移显式且回退不放宽 |
| 3 | `role-model-assignment` | NEW | `openspec/specs/role-model-assignment/` | 允许模型与可选默认之分离、复用原选择链、fallback 持续校验、租户/能力隔离 |
| 4 | `resource-execution-authorization` | NEW | `openspec/specs/resource-execution-authorization/` | 技能/工具/Agent 装配与执行守卫、委派权限、撤权阻止后续、集成与开放分别验收 |
| 5 | `role-authorization-console` | NEW | `openspec/specs/role-authorization-console/` | 现有角色界面统一配置五类资源、平台 all 与目标角色分开、草稿校验、错误处理 |
| 6 | `business-permission-catalog` | MODIFIED | `openspec/specs/business-permission-catalog/` | 权限目录有限且稳定、内置角色显式默认、平台/租户资格独立、多角色实时合并、不开放延期消费者 |
| 7 | `rbac-authorization` | MODIFIED | `openspec/specs/rbac-authorization/` | 平台/租户边界、固定目录与最小角色、页面能力与接口同一规则 |
| 8 | `console-navigation-availability` | MODIFIED | `openspec/specs/console-navigation-availability/` | 导航消费权威摘要、平台/个人作用域独立、各入口一致可达、旧模式边界 |
| 9 | `tenant-skills-tools-console` | MODIFIED | `openspec/specs/tenant-skills-tools-console/` | 技能/工具读写与启停独立、缺授权拒绝（原 agent.read 拒链移除） |
| 10 | `identity-management-workbench` | MODIFIED | `openspec/specs/identity-management-workbench/` | 角色页支持分组权限与成员关联、资源/模型选择 |

---

## 6. 验收范围与未交付项

### 6.1 已交付（本 change，组件集成）
- 五类资源授权接口 + 两张 grant 表 + `model_defaults_json`。
- 角色 CRUD 统一保存 grants/默认模型；平台目标角色薄层适配。
- 技能/工具/Agent/模型**实际调用点**守卫（`skill.use/read/edit/enable`、`tool.execute`、`agent.*`、`model.use`）。
- 菜单/页签投影与 `/auth/context` 能力报告。
- 迁移/回退机制与守卫版本启动。
- 只读预检工具 `scripts/auth_preflight.py` + 授权映射预览。

### 6.2 未交付（延期，明确排除）
- **线上运行开放**：database 的 Web 聊天、流/轮询/取消、消息通道、定时任务执行。
- **高级模型策略**：`fixed`/`priority`、跨角色模型合并预览、独立成员权限预览中心。
- **独立授权缓存 revision / staged-enforced 状态机**（首期不缓存跨请求授权）。

---

## 7. 结尾

本文只读地记录了「已集成什么、如何迁移/回退、证据在哪、哪些未开放」。任何「线上 Web 运行可用」的结论都需要额外端到端验收，不能由组件集成测试代替。归档 change 时同步上述 10 份规范到主规范目录（`openspec/specs/`），不改其他 change 的任务状态。

## 8. 本 change 之上的增量与复核（2026-09-16）

| 项 | 状态 | 依据 |
| --- | --- | --- |
| 菜单 grant 的幂等映射（旧 `nav:personal.*` → 正式页，多对一） | ✅ 已验收 | `_migration_26`（`auth/store.py:1348-1428`）+ `evidence/2-4-menu-mapping-migration.md`；演练与变异见 `tests/test_console_migration_drill.py` |
| 用户默认与租户默认分权（用户默认不再要求管理资格，租户默认仍要求且拒绝私有目标） | ✅ 已验收 | `auth/service.py:861`（`set_user_default_agent`）、`:1603`（`set_tenant_default_agent`）；`evidence/4-4-*`、`4-6-*` |
| 按 owner/scope 的对象范围判定（owner 检查先于管理员例外；公共配置需管理资格本身） | ✅ 已验收 | `auth/object_scope.py`（`allows_agent` `:117`、`allows_public_configuration` `:183`）+ `tests/test_object_scope.py` 20 项 |
| 旧个人入口的兼容转接与调用观测 | ✅ 已验收 | `evidence/8-1-legacy-personal-address-forward.md`、`8-2-compat-cycle.md` |
| §6.2 的三项延期项 | ⬜ 仍为未交付 | 复核未发现 `resource_catalog`、`model_policies`、`authorization_revision`、`AuthorizationService` 的实现 |
| 线上运行开放（本文最强调的边界） | ⬜ 未覆盖 | 真实渠道运行阻塞于真实凭据与真实运行进程（`evidence/7-1-runtime-preflight.md`）；本人连接的运行开关与验收集为空 |

**取代关系**：本文的「组件集成 vs 线上开放」二分法保留且继续有效；被本 change 取代的只是
「控制台仅管理员可用」与「个人入口独立成消费者」两条前提。逐行矩阵见
[`evidence/8-5-doc-closure.md`](../../openspec/changes/unify-console-by-data-scope/evidence/8-5-doc-closure.md) §3。
