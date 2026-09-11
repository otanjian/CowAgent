## Context

动机见 `proposal.md` 的 Why。当前接入点：

- `channel/web/static/js/identity-admin.js` 的 `fieldHtml()` 是一个按 `f.type` 分派的渲染器，已支持 `checkbox` / `textarea` / `select` / `multi` / `locked` / `resourcegroup` / `modeldefaults` / `modelgrant` 与默认文本输入；`select` 直接渲染成静态 `<select>`，没有异步候选来源。
- `openAdminModal()` 在 `body.innerHTML = renderModalBody(...)` 之后逐字段拿到容器节点绑定 `markModalDirty`，并对 `resourcegroup` / `modeldefaults` / `modelgrant` 调用对应的 `_initXxx(node)` 做异步初始化；`collectField(f)` 对这三类走专用收集函数，其余按类型从 DOM 取值。
- `openTenantAdmin(id)` 目前定义 `{ name: 'user_id', type: 'text', required: true }`，提交时 `submitAdminModal()` 的通用必填校验只判断值非空，因此任何非空字符串都会发往 `POST /api/platform/tenants/{id}/admins`。
- 服务端 `set_tenant_admin()` 以 `SELECT * FROM users WHERE id=?` 查目标账号，未命中或 `active=0` 均抛出同一条 `user not found or disabled`（404），不区分「ID 不存在」与「账号已停用」。
- 可复用的读取端点：`GET /api/platform/users`（`PlatformUsersHandler.GET`）已支持 `q` / `status` / `page` / `page_size` 并返回 `{items, total, page}`，项内含 `id` / `username` / `display_name` / `active` / `is_platform_admin` / `must_change_password`；`status=active` 与 `q` 均为服务端过滤，`page_size` 上限 100。
- 测试基建：`tests/test_tenant_create_frontend.cjs` 用 `node:test` + `vm.runInNewContext` 加载真实的 `identity-admin.js`，自带 `element()` / `document` / `fetch` 桩与 `flush()` 微任务推进；新增用例沿用该模式，不引入浏览器依赖。

## Goals / Non-Goals

**Goals:**

- 让「配置租户管理员」的目标账号从当前有效账号中选定，操作者不需接触内部 User ID。
- 在通用对话框字段体系中新增一个可复用的异步候选选择器，而不是为租户视角写一次性 DOM 代码。
- 候选查询失败、候选为空、未选择时都表现为「阻止提交 + 可见错误」，不产生静默降级或错误提交。
- 服务端与既有授权/审计/近期密码/最后管理员约束零改动，降低回归面。

**Non-Goals:**

- 不新增「平台新建账号」端点或页面；本次不闭合「全新部署缺少可绑定账号」的缺口。
- 不预选当前管理员（`set_tenant_admin` 无「替换」语义，且租户列表未返回管理员的 User ID）。
- 不改 `set_tenant_admin` 的校验、授权、审计或错误码，也不新增 `q` / 分页之外的查询能力。
- 不改 `identity.db` 表结构，不改 `auth/http_policy.py` 的 `platform` 策略。

## Decisions

1. **在通用字段渲染器内新增 `userpicker` 类型，而不是给 `select` 加异步能力**
   选择器需要异步候选、搜索框与超页提示，把 `select` 扩展成「同步或异步」会让既有 6 处 `select` 调用点承担空值/加载态语义。新增独立类型可保持既有 `select` 行为不变，并沿用 `resourcegroup` / `modeldefaults` 已确立的「渲染 + `_initXxx` 异步初始化 + 专用 `collectField`」三段式。
   备选「把候选渲染进 `select` 的静态 options」：无法承载超过单页的账号，且候选加载失败时无法与「空列表」区分。

2. **候选集为全局 active 账号，不过滤成员关系**
   `set_tenant_admin` 对已是本租户成员的账号等价于授予/恢复 `tenant_admin` 角色，对属其他租户的账号只增加一个 membership 并保持其原租户资料不变；两者都是契约内的合法操作。过滤会引入一次额外的成员关系查询，并把「我把这个人漏了」变成新的失败模式。
   备选「排除已是本租户管理员的账号」：需要额外读取租户管理员集合（当前列表接口不返回），收益仅是减少重复绑定，不足以引入新读取面。

3. **提交值取稳定 User ID，展示「显示名 · 用户名」**
   服务端契约按 `id` 解析，提交值与展示值必须解耦；沿用平台用户列表已有的展示组合（`identity-admin.js` 的 `loadPlatformUsersView` 采用 `display_name` + `username`）。
   备选「提交用户名再由后端解析」：会改动 `set_tenant_admin` 并重新审视用户名唯一性与改名语义，超出本次范围。

4. **搜索走服务端 `q`，`page_size` 取上限 100**
   一次最多展示 100 个候选，超过时显示「结果已截断，请用搜索缩小范围」而不是静默只显示前 100。输入去抖复用既有 `loadPlatformUsersView` 的 300ms 做法，避免输入即打满请求。
   备选「客户端全量拉取并本地过滤」：`/api/platform/users` 的 `page_size` 硬上限为 100，无法全量。

5. **失败与未选择一律阻止提交，无文本降级**
   选择器把「已选中的 User ID」存于其状态容器；`collectField` 对 `userpicker` 返回该状态而非输入框 `value`。加载失败或未选中时返回空值，从而被 `submitAdminModal()` 既有必填校验拦下，并叠加一条更具体的错误提示。
   备选「失败时退回文本输入」：会让操作者再次面对 `user not found or disabled`，正是本次要消除的问题，违背 spec 中「MUST NOT 静默退化」。

6. **`display_name` 保持自由文本**
   该字段写入的是 membership 的显示名（`memberships.display_name`），不是账号属性；账号自身的 `display_name` 属于平台用户管理。保持现状可避免把账号改名能力顺带带入租户视角。

7. **i18n 复用与新增的边界**
   保留 `admin_field_admin_user_id` / `admin_field_admin_user_id_hint` 的既有键（成员表单与三套语言字典复用），仅替换该字段的渲染类型与提示文案；为选择器的加载中/空态/失败/搜索占位/超页提示在三套语言字典中新增键。不删除既有键。

## Risks / Trade-offs

- [对话框新增一次 `GET /api/platform/users` 请求，平台用户量大时首次加载有延迟] → 取 `status=active` + `page_size=100` 并在容器内显示加载态；候选加载与对话框渲染解耦，不阻塞其余字段填写。
- [候选集全局可见，可能暴露其他租户账号的存在] → 该端点的授权门槛与被它服务的对话框完全一致（均为平台管理员），且平台用户列表本身已对同一角色开放同一份数据，本次不扩大可见面。
- [操作者误把别的租户账号选为管理员，造成跨租户成员关系] → 这是 `set_tenant_admin` 的既有合法语义，且会写入审计；本次不改该语义，仅在提示中说明候选为全局有效账号。
- [新字段类型若 `collectField` 未覆盖，会退化为读取不存在的 `input.value` 并发出空值] → 任务中显式要求 `collectField` 对 `userpicker` 走专用分支，并以「未选择不发请求」「选中后 body.user_id 为所选 ID」两条用例锁定。
- [搜索触发的并发响应乱序，旧结果覆盖新结果] → `apiFetch` 的 `stale-response` 仅在 `_generation` 变化（切换租户）时生效，不覆盖同租户内的请求乱序；选择器自身维护单调递增的请求序号，只应用最新一次请求的结果。
- [选择器加载失败被渲染成空候选，操作者以为系统里没有账号] → spec 已把两者区分为「可重试错误」与「空态」，用例分别覆盖。

## Migration Plan

无数据迁移：表结构、端点与权限均不变，纯前端行为变更。部署只需与静态资源同批发布；回滚只需回退 `identity-admin.js` / `console.js` 与测试，期间已建立的管理员关系不受影响。已在「无管理员」状态下停留的租户（例如 `test02`）在发布后可直接通过选择器补齐管理员，无需数据修复。

## Open Questions

无。候选集范围、提交值、搜索方式、失败语义与是否预选当前管理员均已确认。
