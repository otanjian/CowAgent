## Context

动机见 `proposal.md` 的 Why。当前接入点与约束：

**页面与列表**
- `channel/web/chat.html` 的 `#view-tenant`（约 2107–2122 行）目前只有标题、创建按钮、搜索框、租户列表、空态与状态行，没有编辑器容器；同文件内的角色视图已有整页编辑器先例。
- `channel/web/static/js/identity-admin.js` 的 `loadTenantView()`（1088–1132）渲染列表行并为每行渲染 `edit` 与 `admin` 两个按钮，经 `adminRowAction('tenant', action, id)`（2518–2543）分派。

**弹窗体系（本次要替换的部分）**
- 通用字段渲染器 `fieldHtml()`（232–294）按 `f.type` 分派；异步字段遵循「渲染 + `_initXxx(node)` 初始化 + `collectField()`（777–800）专用收集」三段式；`openAdminModal()`（802–852）与 `submitAdminModal()`（876–923）提供 `submit` / `onConflictReload` / `afterSuccess` 生命周期钩子。
- `openTenantCreate()`（1161–1199）：字段 `code` / `name` / `recent_password`，成功后经 `afterSuccess` 接续 `openTenantAdmin(createdId)`。
- `openTenantEdit(id)`（1200–1251）：字段 `name` / `active` / `model_grants` / `recent_password`；提交时先 `POST /api/platform/tenants/{id}` 再 `PUT /api/platform/tenants/{id}/resources`。
- `openTenantAdmin(id)`（1253–1273）：字段 `user_id`（`userpicker`）/ `display_name` / `recent_password`，提交 `POST /api/platform/tenants/{id}/admins`。
- 租户模型选择器 `tenantModelGrantHtml()`（533–563）/ `_collectTenantModelGrants()`（565–572）/ `_initTenantModelGrant()`（574–642），状态存于 `_tenantGrantSel` / `_tenantGrantCatalog` / `_tenantGrantApiBase` / `_tenantGrantVersion`（160–165），**kind 硬编码为 `model`**。
- 角色资源选择器 `resourceGroupHtml()`（335–352）/ `_initResourceGroup()`（733–748）/ `_collectResourceGrants()`（486–499），动作集在 `_resourceActions`（141–149），kind 列表在 `_resourceKinds`（147–149）。

**整页标签页既有先例**
- 角色编辑器使用 `.role-editor` 容器 + `.role-editor-tabs` / `.role-editor-tab[data-tab]` / `.role-editor-panel#role-panel-<tab>`，切换由 `switchRoleEditorTab(tab)`（1899–1911）完成；样式在 `channel/web/static/css/console.css`（4442–4521）。

**后端**
- `channel/web/admin_handlers.py` 的 `PlatformTenantHandler.POST`（290–318）以 `if "name" in data` 决定调用 `set_tenant_name` 还是 `set_tenant_status`；由于 `openTenantEdit` 总是发送 `name`，`set_tenant_status` 分支实际不可达。
- 已具备且本次不改的端点：`PlatformTenantResourcesHandler`（825–858，`GET/PUT {id}/resources`）、`PlatformTenantAuthorizationCatalogHandler`（794–822，all_mode 目录）、`PlatformTenantAdminsHandler`（321–344）。
- `auth/service.py`：`create_tenant`（1822）、`set_tenant_name`（2046）、`set_tenant_status`（1949）、`set_tenant_admin`（1989）、`tenant_resource_grants`（814）、`set_tenant_resource_grants`（817）、`authorization_catalog`（1018）、`grantable_resource_ids`（742）、`check_resource_action`（692）。
- `tenant_resource_grants` 表已存在（`auth/store.py:232`），支持任意 `resource_kind`，因此新增工具上限**不需要 schema 变更**。
- 决定标签划分的关键约束：`_project_owned_ids(tenant_id, kind)`（1002）对 `model` 与 `tool` 返回 `None`，于是 `grantable_resource_ids` 只剩租户级 grant —— 缺少租户上限入口会让租户管理员无法分配该类资源；对 `skill` 返回全部已投影技能，技能因此**不需要**租户级上限入口。

## Goals / Non-Goals

**Goals:**

- 提供一个可复用的整页标签编辑器骨架（顶栏标识 + 标签条 + 面板 + 底部保存栏），承载租户配置，并保持与角色编辑器一致的视觉与切换契约。
- 把租户级资源上限选择器从「模型专用」泛化为「按 `resource_kind` 参数化」，使模型与工具两个标签共享同一份分页、搜索、全选、清空与冲突处理实现。
- 让「租户名称 + 是否启用」在一次事务内原子提交，消除部分成功窗口并修正既有缺陷。
- 让新建租户的后续配置步骤（管理员指定）在同一页面内可发现。
- 只增加平台可见的入口，不放宽任何既有授权语义。

**Non-Goals:**

- 不重构通用 `openAdminModal` 弹窗体系；成员、角色等视图继续使用它。
- 不把租户列表改为内联编辑，也不为标签引入新的路由、视图标识或深链接。
- 不引入「租户空间」实体，也不提供租户空间的任何写操作。
- 不改 `role-resource-authorization` 已定义的运行时生效规则与租户上限约束语义。
- 不新增平台级「新建账号」端点。

## Decisions

1. **整页编辑器，而不是在弹窗内加标签**
   需要同时容纳两个可分页的选择列表与管理员配置，弹窗的宽度与滚动高度已接近极限；仓库内已有 `.role-editor` 整页先例，DOM 与 CSS 词汇可直接复用。
   备选「继续用 `openAdminModal` + 标签」：分页列表在弹窗内嵌滚动会重复出现高度/焦点问题，且弹窗的 `dirty`/`afterSuccess` 生命周期并不适合多标签。

2. **标签各自独立提交，而不是顶部统一保存**
   后端本就是三个独立领域（租户生命周期 / 租户级资源上限 / 管理员配置），且两类 `expected_version` 语义不同（租户版本 vs 资源上限版本）。独立提交让失败面收敛到单个标签，无需设计「部分成功」。
   备选「统一保存」：需要前端顺序提交并在中途失败时决定是否回滚已成功的部分，产生难以向使用者解释的中间态。

3. **新增 `set_tenant_profile` 单事务合并写，而不是前端发两次请求**
   单事务内同时更新 `name` 与 `active`，版本只递增一次、只写一条审计，并复用 `set_tenant_status` 的既有守卫（`_count_valid_tenant_admins`、`_check_all_affected_active_tenant_continuity`）。这样「同时改名并恢复」要么整体成功，要么整体不变。
   备选「前端先改名再改状态，版本 +1/+1」：存在「名称已提交、启用未提交」的部分成功窗口，且两条审计让使用者难以判断真实终点。
   备选「每次只允许修改一项」：无法解决并发下的原子性，且体验更差。

4. **Handler 使用显式 `operation` 字段，并保留旧派发回退**
   键存在性派发是本次缺陷的根因，也无法表达「同时改名与改启用」。改为显式 `operation`（`profile` / `name` / `status`）后可读且可扩展；无 `operation` 的请求继续按 `name` 键派发，避免破坏 CLI 与缓存了旧静态资源的客户端。
   备选「直接移除旧派发」：会让未同步更新的调用方行为变化，收益不足。

5. **资源上限选择器按 `resource_kind` 参数化，而不是复制一份工具选择器**
   两个标签的交互（搜索、分页、按页全选、清空、整体替换、409 冲突处理）完全同构，差异仅在 `resource_kind`、动作集与展示来源标注。复制实现会产出两份需要同步维护的分页与错误处理。
   备选「为工具单独写一份」：短期改动小，长期必然漂移。

6. **租户上限授予该资源类别的全部合法动作**
   `grantable_resource_ids` 按 action 分别计算，租户上限缺少某个 action 会使租户管理员无法把该 action 分配给角色（`tool.configure` 即属此类）。因此模型类别授予 `read` + `use`，工具类别授予 `read` + `execute` + `configure`，与角色编辑器的 `_resourceActions` 保持一致。
   备选「工具只授予 read + execute」：会让工具配置能力在租户内永远不可分配，且与角色编辑器的动作集不一致。

7. **租户空间呈现为只读信息卡**
   代码中租户空间即 `tenants.shared_root`，由创建流程派生，且 `tenant-management` 既有规范明确要求不向客户端返回宿主绝对目录。只读呈现既满足「页面能看到租户空间」的诉求，又不引入目录生命周期、清理与跨租户包含校验的新问题。
   备选「展示真实路径」：违反既有规范，且把宿主布局暴露给控制台使用者。

8. **复用 `userpicker` 与既有管理员绑定端点**
   用户已明确本标签只需「指定租户管理员」。既有 `set_tenant_admin` 的语义与 `userpicker` 的候选查询已验收，复用可避免新增权限边界与审计归属决策。
   备选「新增平台建号端点」：`create_member` 的 HTTP 入口要求调用者是该租户的 `tenant_admin`，平台管理员对新租户没有成员身份；放宽该门槛会改变成员创建的授权语义，超出本次范围。

9. **新建模式只开放「基本信息」标签**
   资源上限与管理员配置都需要已存在的租户标识作为提交目标。允许预填需要暂存草稿并在创建后重放，收益不抵复杂度。
   备选「允许预填后续标签」：需要跨创建与编辑两个阶段的草稿协议，且与「单标签独立提交」的原子性假设冲突。

## Risks / Trade-offs

- [整页编辑器与角色编辑器的样式/行为重复，形成两套需要同步的标签实现] → 优先复用 `role-editor-*` 的 CSS 类与 `switchXxxTab()` 切换契约；若类名语义不符则在 `console.css` 以同一词汇新增 `.tenant-editor-*` 规则块，不整段复制既有样式。
- [`set_tenant_profile` 与 `set_tenant_name` / `set_tenant_status` 的守卫逻辑重复] → profile 分支复用同一组守卫辅助方法，不复制守卫代码；并补一条「同时改名并恢复但缺少有效 tenant_admin 必须整体拒绝」的回归用例。
- [启用状态修复后暴露既有数据不一致（历史界面显示停用但 `active=1`）] → 改动只影响后续提交，不做自动数据修复；迁移计划中列出发布后的核对方式，不一致项交运维确认。
- [工具上限入口开放后租户内工具权限面看起来变大] → 只是把「平台显式授予」变为可达；未勾选的工具仍不可分配（已有场景锁定），运行时仍受 `check_resource_action` 与工具侧拒绝逻辑双重校验。
- [缓存了旧静态资源的客户端仍走旧弹窗，表现为「启用状态依旧不保存」] → 后端保留无 `operation` 的旧派发，旧前端不会报错；发布说明要求刷新静态资源。
- [两个在途 change 对同一接续行为作出相反断言] → 本 change 只把「创建成功后自动弹出管理员对话框」替换为「停在编辑器并提示跳转标签 4」，不改创建请求契约；实现时必须同步更新 `simplify-tenant-creation-form` 引入的前端用例断言。
- [租户列表行按钮收敛破坏既有前端用例] → `tests/test_tenant_create_frontend.cjs` 断言行内存在 `edit` 与 `admin` 两个按钮；实现时同步更新该断言，并在 tasks 中显式列为必做项。
- [各标签各自收集 `recent_password` 增加重复输入] → 密码不跨标签缓存到持久状态，只随各标签的提交即时使用；若实测繁琐可改为编辑器级单次提示，属可延后调整的实施参数。

## Migration Plan

**数据迁移：无。** `identity.db` 表结构与既有行不变；`tenant_resource_grants` 原样复用；`set_tenant_profile` 只是既有两个写路径在同一事务内的合并，不新增表或列。

**部署顺序：** 后端（`auth/service.py`、`channel/web/admin_handlers.py`）与静态资源同批发布。由于保留了无 `operation` 的旧派发，先发布后端不会破坏仍在使用旧弹窗的前端。

**回滚：** 只需回退代码。期间经 `set_tenant_profile` 提交的名称与启用状态仍可由旧的 `set_tenant_name` / `set_tenant_status` 逐项修改；经工具上限提交的数据只是 `tenant_resource_grants` 行，可由 API 调整。不产生不可恢复状态。

**发布后核对：** 逐个核对租户列表显示的启用状态与 `tenants.active` 是否一致，列出历史不一致项交运维确认，本次不自动改写。

**归档顺序：** `tenant-admin-account-picker` 与本 change 都修改 `tenant-management` 的「租户写入使用版本与同库审计」。本 change 的 delta 已把该 change 的增量合并进同一段落，但归档顺序仍 MUST 为 `tenant-admin-account-picker` 先、本 change 后；顺序颠倒会让本 change 的 delta 覆盖掉前者新增的候选账号读取语义。`simplify-tenant-creation-form` 与本 change 的 delta 无重叠，只在前端接续行为与其用例断言上冲突，需在实现时同步更新。

## Open Questions

- 各标签内 `recent_password` 的收集频率（每标签一次 vs 编辑器级一次）可先按「每标签一次」实现，若实测繁琐再调整；不影响规范与任务拆分。
- 租户空间「可用状态」的判定口径（仅表示目录存在且通过既有跨租户包含校验，还是额外纳入读写探测）可先按前者实现，后续按运维反馈细化；不影响规范与任务拆分。
