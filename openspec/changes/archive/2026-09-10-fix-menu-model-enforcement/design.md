## Context

见 `proposal.md - Why`。本设计只描述如何把已存在的授权数据接入两条读路径，并说明取舍。

当前代码事实（已实测）：

- `auth/service.py::_console_pages_projection` 对 `_SIGNED_CONSOLE_PAGES` 的页面只用功能权限算 `read_allowed`（`read_ok`），从不读取 `menu` grant；`menu`/`nav:*` 仅在角色编辑器目录（`_resource_source_projection` / `authorization_catalog`）被读取。
- `channel/web/static/js/console.js` 的 `_viewNavDenied` 与 `_applySidebarPermissions` 显式跳过所有非 `admin.*` 页面；`#sidebar-recent`（i18n `sidebar_history_records` = 「会话历史」）是 `chat.html` 静态区块，无 `data-view`，除 area 判断外无任何门控。
- `channel/web/web_channel.py::SessionSettingsHandler.GET` 未使用 `_db_scope()`，导致 `_authorized_model_codes()`（`web_channel.py:594`）内 `current_identity()` 为空并返回 `None`（被当作无限制）；`POST` 使用 `_db_scope()` + `_require_model_use`，鉴权正常。
- `identity.db` 中内置 `tenant_admin`/`member` 角色 `menu` grant 数为 0；仅自定义角色（如 `test15-1`）有显式 `menu` grant。

## Goals / Non-Goals

**Goals:**
- 让 `menu` grant 真正参与页面可见性与直达判定，使「未分配会话历史菜单权限」不再显示该入口。
- 让受限成员的会话模型选择器只呈现授权模型，且继承到的模型受允许集合约束。
- 保持内置角色与历史遗留（无 `menu` grant）角色的既有行为，避免迁移期误隐藏。

**Non-Goals:**
- 不新增 schema、不新增 API、不改变 grant 的存储与授予流程。
- 不开启任何尚未开放的运行消费者，不改动 `platform all` 语义与 `resource-execution-authorization` 的执行守卫。
- 不在本次为每类资源补齐"能力投影缓存"/`authorization_revision`。

## Decisions

### D1：菜单授权采用"存在即强制"的兼容式规则

**决定**：在 `_console_pages_projection` 中计算菜单门槛：

```
mode == "all"                       -> 不受限
成员有效角色存在 >=1 条 menu grant  -> 受 nav:{pid} 约束
否则（完全没有 menu grant）          -> 不受限   # 兼容回退：沿用功能权限行为
```

实现为**最后一次统一过滤**：对 `result` 中每个已登记页面，若身份受 menu 约束且缺 `nav:{pid}`，则该页 `read_allowed=False`、`available=False`、`reason="menu_not_granted"`、`actions={}`，并额外标记 `menu_denied=True`；否则页面保持原有功能权限/消费者状态计算。`available` 在未命中 menu 规则时仍由消费者开放状态决定。

> 实现注记：最终采用统一后置过滤而不是在各个分支逐点接入，覆盖工作台页、generic 页与 `identity_admin_open` 分支的 `admin.members/roles/organization`，避免遗漏；`admin.tenants` 未被 menu 规则改写（仅平台身份决定）。

**前端契约**：`menu_denied` 是"该页因菜单授权被拒"的权威信号，前端据此拒绝/隐藏，不再自行推断。

**理由**：`identity.db` 中内置角色 `menu` grant 数为 0，若严格强制会让内置成员看不到任何菜单；历史遗留自定义角色同样没有 grant。兼容式规则精确修复 `test15-2`（其角色**有** grant 集合且缺 history），同时对无 grant 角色零影响。

**备选**：(a) 严格强制 + 迁移补齐内置角色 grant：改动面大、迁移风险高；(b) 直接从菜单目录移除 workbench 页：与规范"菜单 view 授权决定可达性"冲突，且用户预期是授权生效。

**影响范围**：所有已登记页；`test15-2` 未授 `nav:admin.members/roles/organization`，将不再显示对应入口；`admin.tenants` 仍由平台身份独立判定。

### D2：前端把 workbench 页纳入同一投影门控

**决定**：
- `_viewNavDenied` 对投影中存在且 `menu_denied === true` 的页面直接返回拒绝（含 workbench 页）；其余非 `admin.*` 页仍按消费者状态放行。
- `_applySidebarPermissions` 对 `.sidebar-item[data-view]`：先按 `menu_denied` 隐藏；非 `admin.*` 页不再套用 admin 的 available/read 门槛。
- `#sidebar-recent`（会话历史区块）纳入门控：`_sidebarRecentDenied()` 复用 `_viewNavDenied('history')`；不可达时隐藏整块，`loadSidebarRecentSessions()` 直接返回、不发起 `/api/sessions` 请求。
- **不**给 `#sidebar-recent` 增加 `data-view="history"`：既有前端契约（`tests/test_nav_area_frontend.cjs`）明确规定 `data-view="history"` 必须不存在（顶栏历史入口已折叠进该区块）。改为按元素 id 显式门控，效果一致且不破坏该契约。

**理由**：投影由服务端计算，前端不再另建真值；区块隐藏与请求抑制都只依赖投影，符合"不初始化拒绝目标的请求"。

**备选**：仅在后端拒绝 `/api/sessions`——会造成"菜单可见但数据 403"的割裂，不符合规范的"不可达即不显示、不初始化"。

### D3：会话模型选择在身份作用域内计算并 fail-closed

**决定**：
- `SessionSettingsHandler.GET` 用 `with _db_scope() as ctx:` 包住现有逻辑（与 POST 对齐），legacy 模式 `_db_scope()` 返回 `None`，行为不变。
- `_authorized_model_codes()` 在 database 模式下若解析不到 `user_id`/`tenant_id`，返回空集合 `set()` 而非 `None`；只有 legacy 模式才返回 `None`（无限制）。

**理由**：`None` 现被同时用于"legacy 无限制"和"作用域缺失"，是本次泄露的直接原因；区分后旧模式不受影响，database 缺失则 fail-closed。

**备选**：在 `_session_settings_state` 里各自判断身份——分散且易漏，集中在该 helper 更安全。

### D4：继承模型必须落在允许集合内

**决定**：在 `_session_settings_state` 解析出 `effective_model` 后，若允许集合存在（非 `None`）且（集合为空 或 `effective_model` 的末段 code 不在集合内）：
- 不再把它作为生效模型展示：返回 `model=""`、`source="unset"`、`selection_required=True`；
- 候选 `providers` 只保留允许集合内的模型。
- 角色默认模型以 `provider:{pid}:{code}` 资源 id 存储，比较时取末段 code。
- 会话显式选择越权时仍由 POST 的 `_require_model_use` 拒绝（已有行为）。

**理由**：仅过滤列表会让"生效值仍是未授权模型"，与规范"实际请求与备用模型持续校验"不一致。

**备选**：静默改投到第一个授权模型——规范明确禁止"静默改投"。

**前端表现**：`selection_required=true` 时模型 chip 走 `model_unset`（未配置）展示，提示用户重新选择；候选菜单只列授权模型。

**边界**：本次只收口到"会话设置读取/展示 + 显式选择"；聊天运行时的每次模型调用守卫沿用 `resource-execution-authorization` 的既有实现，不在本次扩张。

## Risks / Trade-offs

- [收紧后自定义角色可能立即少显示若干入口] → 兼容式规则把影响限定在"有显式 menu grant 的角色"；这类角色是可编辑对象，管理员可在角色编辑器补齐授权。
- [`admin.members/roles/organization` 纳入 menu 过滤改变既有可见性] → 与规范一致；受影响的是"有 menu grant 但未授该页"的自定义角色，属预期收紧。
- [GET 增加 `_db_scope()` 可能在极端情况下改变无租户请求的行为] → `_db_scope()` 在 legacy 下为 no-op，在 database 下与 POST 完全一致，属正确对齐。
- [继承模型重选可能影响首次进入会话的体验] → 只在继承模型确实越权时触发，且仅对受限角色；平台 all 与无限制身份不受影响。

## Migration Plan

无 schema 迁移。部署即生效：

1. 先上前端+后端同一构建；`/auth/context` 新增语义由服务端计算，旧前端不消费也安全（可见性暂不收紧，不会放开权限）。
2. 回退到旧构建即可恢复旧可见性；授权数据不变，不出现"grant 表缺失=全放行"。
3. 建议部署后按验收清单复核：受限自定义角色、内置角色、平台管理员三类身份的菜单与模型选择器。

## Open Questions

无。内置角色与历史角色的兼容规则已在 D1 明确。
