## Why

平台管理员当前由 `users.is_platform_admin` 布尔列标识，是一处与 `roles` 并行、不可枚举、不可在角色模型中表达的隐式身份。这使平台资格无法像租户角色那样作为「一个角色」被枚举与理解，资格的授予/撤销走的是与租户 RBAC 割裂的独立写路径，且该列被约 42 处服务层引用直接读取，资格来源缺乏单一事实点。

本 change 把平台管理员改为**平台级内置角色**（平台作用域角色 + 用户绑定表），绑定关系成为资格的**唯一来源**；同时在同一写事务内维护 `users.is_platform_admin` 作为**派生镜像**，使现有 API 与前端契约（`/auth/*` 响应、`RequestContext.is_platform_admin`、平台账号编辑开关、平台侧栏显隐）保持不变，迁移对调用方透明。

**范围提示**：本 change 只做「底层把平台角色作为唯一来源」的重构，用户在 UI 上看不到变化——「平台管理员以角色形式在角色页展示」属独立前端 change，不在本 change 内。

需求基线参照 `doc/优化规划/PRD/PRD-00-总目录.md` 及 PRD-01～12 v1.2；仓库当前未包含该目录原文，本 proposal 不宣称已逐条核验原文，仅依据现有代码、`openspec/specs/` 既有规范与设计文档。本 change 承接既有 `platform-all-authorization`、`rbac-authorization`、`business-permission-catalog` 能力，不引入新的业务域。

## What Changes

- 新增平台作用域数据模型：`platform_roles`（平台内置角色，含 `platform_admin`）与 `user_platform_roles`（账号到平台角色的绑定），并新增附加式 schema 迁移执行 seed 与存量回填。
- 平台管理员资格判定改为读取平台角色绑定（`platform_admin`），覆盖 `auth/policy.py`、`auth/service.py` 的全部谓词、连续性与计数逻辑，以及 `auth/runtime.py` 的上下文派生。
- 保留 `users.is_platform_admin` 为派生镜像字段：唯一写入路径收敛到一个同事务的内部方法（增删绑定 + 同步镜像）；对外部调用方、API 响应与前端**契约不变**。
- 修正 `revalidate_context` 原样复制旧平台资格、导致「一旦被复用将延续旧资格」的正确性缺陷（生产路径当前不经过它，属正确性/测试卫生修复，非生产安全修复）。
- 普通角色保存接口继续拒绝平台身份标识、通配符与 `all`；平台角色为独立作用域，不进入 `member` / `tenant_admin` 的默认权限并集。
- 归档既有非规范设计文档中「不把平台管理员伪装成普通角色记录」的相反表述，改为记录角色化决策与理由。

## Capabilities

### New Capabilities

无新增能力。

### Modified Capabilities

- `platform-all-authorization`：将「服务端验证的平台管理员资格」明确为**平台级内置角色绑定派生**，补充资格授予/撤销的 WHEN/THEN 场景，保留 all 语义、目标租户管理分离、不伪造 Membership 与撤销即时生效等既有 SHALL。
- `rbac-authorization`：明确平台资格是**平台作用域内置角色**，与租户 `tenant_admin` / `member` 分开；平台角色的增删只经平台控制面，不得经租户角色或成员接口赋予。
- `business-permission-catalog`：明确平台内置角色独立于成员/租户管理员显式默认集合，不因目录扩充自动扩权；租户角色保存必须拒绝平台角色标识。

## Impact

- 数据：`auth/store.py` 新增迁移（`platform_roles`、`user_platform_roles`、唯一索引、seed、存量 `is_platform_admin=1` 回填与镜像同步）；不改动、不删除既有表与列，旧版本代码可就地回滚。
- 服务层：`auth/policy.py`（平台角色码与默认集合）、`auth/service.py`（谓词、`authorization_mode`、`_require_platform_admin`、连续性计数、`set_platform_user_status`、bootstrap 回填）、`auth/runtime.py`（上下文派生与重新校验）。
- HTTP / CLI：`channel/web/admin_handlers.py`（内部语义切换与死分支清理）、`cli/commands/management.py`（改用服务谓词）；`auth/http_policy.py` 路由策略标签与 14 条 `platform` 路由的门禁行为不变。
- 前端：`channel/web/static/js/console.js`、`channel/web/static/js/identity-admin.js`、`channel/web/chat.html` 契约不变，无必需改动。
- 测试：`tests/test_platform_user_admin.py`、`tests/test_identity_concurrency_acceptance.py`、`tests/test_identity_migration_drill.py`、`tests/test_identity_web_handlers.py` 等需更新为按角色绑定断言，并新增迁移一致性与资格撤销回归。
- 规范与文档：`openspec/specs/platform-all-authorization/spec.md` 等三份规范 delta；修订 `docs/design/role-resource-authorization-plan.md` 的相反决策表述。
- 不依赖在途 change；不改动多租户业务隔离、私有资源 owner、业务会话协议、Desktop 或 Channel。
