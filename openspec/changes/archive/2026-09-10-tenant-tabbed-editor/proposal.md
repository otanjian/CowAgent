## Why

「系统设置 → 租户」的创建与编辑目前复用同一个通用弹窗（`openAdminModal`），把三类语义完全不同的操作挤在一个对话框里：租户生命周期（编码 / 名称 / 启用）、平台维护的租户级资源上限（模型授权）、以及租户管理员配置。由此产生四个具体问题：

1. **「是否启用」从未真正保存过**。`PlatformTenantHandler.POST` 用「请求体是否含 `name` 键」派发 `set_tenant_name` 或 `set_tenant_status`，而编辑弹窗总是发送 `name`，所以 `set_tenant_status` 分支永远不可达；管理员在界面上切换启用状态并保存后，租户状态不变且界面显示成功。
2. **工具能力实际不可用**。`_project_owned_ids` 对 `kind='tool'` 返回 `None`，因此 `grantable_resource_ids` 只剩租户级 grant；而界面上只有模型授权的租户级入口，没有任何工具上限入口。结果是租户管理员无法把任何工具授予角色，工具对租户内成员不可达。
3. **弹窗承载力不足**。编辑弹窗同时承载改名与模型授权勾选列表，字段分组与宽度已经吃紧，无法再容纳工具上限与管理员配置。
4. **管理员配置与租户其余配置分离**。租户管理员被拆成列表行上的独立按钮，与租户对象本身脱节。

租户是多租户体系的第一等对象，需要一页能完成全部配置的编辑器。

## What Changes

- 把租户「新建」「编辑」改为 `#view-tenant` 内的**整页四标签编辑器**（覆盖列表区域，复用现有角色编辑器的 DOM/CSS 模式；不改路由、不改视图标识 `tenant`、不改菜单顺序与面包屑）：**基本信息 / 模型授权 / 工具授权 / 租户管理**。
- 每个标签**独立保存**，各自提交 `expected_version` 与操作者 `recent_password`；切换标签保留未提交草稿并提示「有未保存修改」；单个标签保存失败不丢失其他标签草稿。
- 基本信息标签把「租户名称 + 是否启用」作为**一次提交**保存。**BREAKING（仅 `POST /api/platform/tenants/{id}` 的请求契约）**：新增显式 `operation` 字段（`profile` / `name` / `status`），并在服务端新增 `set_tenant_profile`，在单个事务内同时更新 `name` 与 `active`、版本只递增一次、只写一条审计，且仍分别通过「启用前须有有效 tenant_admin」与「停用不破坏活动成员租户连续性」两个守卫。无 `operation` 的旧请求按原有 `name` 键派发，行为不变。
- 新增「工具授权」标签承载 `resource_kind='tool'` 的租户级上限，与「模型授权」共用同一个按 kind 参数化的选择器；两者都写入 `tenant_resource_grants`，动作集沿用该 kind 的合法动作（模型 `read`/`use`，工具 `read`/`execute`/`configure`）。
- 「租户管理」标签提供「指定租户管理员」入口，复用既有 `set_tenant_admin`（绑定已有有效账号）与 `userpicker` 选择器；并把「租户空间」呈现为**只读信息卡**（空间标识 / 状态 / 隔离类型），不展示宿主绝对路径，也不提供新增或删除入口。
- 移除租户列表行上的独立「管理员」按钮；创建租户成功后停留在编辑器的基本信息标签，并在页内提示下一步为指定租户管理员。
- 新建租户时只有「基本信息」标签可用，其余三个标签呈说明态（租户尚不存在，无对象可挂载）。
- 不新增平台建号端点、不引入「租户空间」实体、不改 `identity.db` 表结构、不放宽停用/恢复的管理员连续性校验强度。

## Capabilities

### New Capabilities

无。本次不引入新能力，只修改既有 `tenant-management` 的租户页面契约。

### Modified Capabilities

- `tenant-management`：把「租户页面区分平台管理与当前租户读取」扩展为整页四标签编辑器契约（标签划分与顺序、各标签独立保存与草稿保留、新建租户时的标签可用性、非平台管理员仍走只读路径）；在「启用状态变更保护租户边界」中明确名称与启用状态可在同一事务内一次提交且版本只递增一次、审计只写一条，同时仍须通过各自的守卫；新增「租户级资源上限按类别在租户页面维护」与「配置租户管理员及租户空间展示」的界面契约。

## Impact

- **代码**：`auth/service.py`（新增 `set_tenant_profile`）、`channel/web/admin_handlers.py`（`PlatformTenantHandler.POST` 增加 `operation` 与 profile 分支）、`channel/web/static/js/identity-admin.js`（新增租户整页编辑器；把 `tenantModelGrant` 选择器泛化为按 kind 参数化；列表行按钮收敛为「编辑」）、`channel/web/chat.html`（`#view-tenant` 内新增编辑器容器）、`channel/web/static/css/console.css`（编辑器样式，优先复用角色编辑器既有类）、`channel/web/static/js/console.js`（三套语言的标签与提示文案键）。
- **API 契约**：`POST /api/platform/tenants/{id}` 新增可选 `operation` 字段；`operation:"profile"` 时同时接受 `name` 与 `active`。`GET/PUT /api/platform/tenants/{id}/resources`、`GET /api/platform/tenants/{id}/authorization/catalog`、`POST /api/platform/tenants/{id}/admins` 契约不变。不新增端点，不改 `auth/http_policy.py` 的路由策略。
- **数据**：`identity.db` 表结构不变。租户级模型/工具上限原本就存放于 `tenant_resource_grants`，本次只是补上工具的界面入口，不产生新的数据归属，也不需要迁移。
- **授权**：全部入口仅平台管理员可达，沿用既有 `_require_platform_admin` 与近期密码校验；前端隐藏标签不构成授权，服务端逐请求校验。
- **兼容**：无 `operation` 字段的旧请求继续按 `name` 键派发，旧客户端与 CLI 调用行为不变；缓存了旧静态资源的前端仍可走原有弹窗路径直到刷新。回滚只需回退代码，期间通过 `set_tenant_profile` 提交的数据仍可由旧接口逐项修改。
- **依赖**：依赖 `simplify-tenant-creation-form`（创建表单字段收敛为 `code`/`name`/`recent_password`）与 `tenant-admin-account-picker`（`userpicker` 字段类型与候选查询）。本 change 把前者的「创建成功后自动弹出管理员对话框」替换为「停在编辑器标签 4 的页内提示」，不改变其创建请求契约。
- **归档顺序约束**：`tenant-admin-account-picker` 同样修改 `tenant-management` 的「租户写入使用版本与同库审计」这一条 requirement，与本 change 重叠。本 change 的 delta 已把该 change 已写入的增量（候选账号加载失败的读取语义与其场景）合并进同一段落，但归档顺序仍 MUST 为 `tenant-admin-account-picker` 先、`tenant-tabbed-editor` 后，否则后者归档时会覆盖前者新增的内容。`simplify-tenant-creation-form` 修改的是「创建租户一次建立可用身份关系」，与本 change 的 delta 无重叠，仅在前端接续行为与用例断言上冲突。
- **跨 change 边界**：租户级上限的运行时生效规则与「普通角色分配受租户资源上限约束」仍归 `role-resource-authorization` 所有，本 change 不复制该规范，只增加平台侧维护入口的界面契约。技能类资源不设租户级上限入口，因为 `_project_owned_ids` 已将所有技能视为租户自有资源。
- **PRD 映射缺口**：仓库所称 PRD-01～12 v1.2 原文仍缺失（`doc/优化规划/` 目录不存在），本次行为基线取自 `openspec/specs/tenant-management/`、`openspec/specs/role-resource-authorization/` 与既有产品代码。PRD 恢复后需核对租户控制台是否另有标签划分、字段清单或工具上限动作集要求。
