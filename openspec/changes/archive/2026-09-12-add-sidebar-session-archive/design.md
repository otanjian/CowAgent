## Context

现状（动机见 proposal.md - Why，需求见 `specs/session-history-workbench/spec.md`）：

- 侧边栏「会话历史」块由 `channel/web/static/js/console.js` 的 `renderSidebarRecentSessions()` 渲染，数据来自 `GET /api/sessions?scope=all&page=1&page_size=10`，每条形如单个 `<button class="sidebar-recent-item">`，点击即 `switchSession()`。
- 完整「历史会话」页的每行已有「更多操作」菜单（置顶 / 重命名 / 删除），经 `_openSessionActionMenu()`；删除走 `SessionDetailHandler.DELETE`，是物理删除（`store.clear_session()` + 取消进行中运行 + 清侧存储）。
- 会话存储 `agent/memory/conversation_store.py` 的表结构由 `agent/memory/conversation_schema.py` 分段组合；`sessions` 当前有 `pinned` 标记但**没有任何归档/隐藏概念**。
- `_init_db()` 先 `CREATE TABLE IF NOT EXISTS`，再 `_migrate()` 调用 `plan_column_migrations()` 对每个缺失的可加列执行 `ALTER TABLE ... ADD COLUMN`，最后建索引。新增可加列会自动落到既有库，无需手写迁移脚本。
- `list_sessions()` 按 `owner` + `dimension_clause(agent_id, tenant_id)` 过滤，支持 `channel_type` 与标题子串 `q`，返回 `total/has_more`；`list_session_ids()` 用于统计项目空间数。跨智能体列表 `_list_sessions_across_agents()` 合并各 store 结果，`SessionsHandler.GET` 直接调用它或单 store。

## Goals / Non-Goals

**Goals:**
- 以最小、可回滚的存储改动引入会话软归档：默认查询隐藏归档会话，显式查询读取归档会话，可恢复且不丢数据。
- 归档入口只加在侧边栏历史列表，逐条一键归档，不影响完整历史页既有操作。
- 归档/恢复沿用既有 `history.read` 授权与目标智能体作用域，不扩大访问范围。
- 归档当前会话与进行中运行保持非破坏性。

**Non-Goals:**
- 不新增独立归档存储、归档时间线或自动归档策略。
- 不在完整「历史会话」页的「更多操作」菜单增加归档（本次仅侧边栏）。
- 不做归档列表的搜索、分页无限加载、批量归档。
- 不改动删除、重命名、置顶的既有语义，也不改会话 ID、消息与项目绑定的存储归属。

## Decisions

### D1. 归档 = `sessions.archived` 可加列（默认 0），复用 schema 分段
在 `conversation_schema.py` 的 `_HISTORICAL_TABLES[SESSIONS]` 增加 `ColumnSpec("archived", "archived INTEGER NOT NULL DEFAULT 0")`（additive）。既有库由 `plan_column_migrations()` 自动 `ALTER TABLE`，既有行自动为 0（未归档），新建库与升级库 schema 一致。
- 理由：归档是会话行自身的过滤属性，放同一张表可让作用域过滤、排序与合并逻辑保持不变；默认 0 保证升级零回填、行为不变。
- 备选 A：独立 `archived_sessions` 关联表。否决——需要 JOIN、破坏 `list_sessions` 的合并/去重与 `total` 计算，改动面与回归风险更大。
- 备选 B：复用 `pinned` 一类的布尔位或负值。否决——语义混淆，无法同时表达置顶与归档。

### D2. 过滤发生在查询层，默认 `archived=0`
`list_sessions(..., archived=False)` 增加 `archived = ?` 子句（与 `q` 叠加），`list_session_ids(..., archived=False)` 同样默认排除归档。两处默认值保证侧边栏、完整历史页、项目空间计数与搜索自动隐藏归档会话；显式 `archived=True` 供归档列表使用。
- 理由：单点过滤、所有现存调用方无需改动即得到「归档即从历史消失」；`total/has_more` 天然按过滤后集合计算。
- 备选：前端过滤。否决——会污染总量、分页与跨智能体合并，且失效于其他客户端。

### D3. HTTP 复用 `PUT /api/sessions/{id}`，列表复用 `GET /api/sessions`
`SessionDetailHandler.PUT` 请求体新增 `archived` 布尔（与 `title`/`pinned` 同路，沿用 `_require_read_permission(ctx, "history.read")` 与 `_require_session_scope`），`SessionsHandler.GET` 新增 `archived` 查询参数并透传 `_list_sessions_across_agents` / `store.list_sessions`。
- 理由：会话的状态变更已集中在 `PUT /api/sessions/{id}`，无新路由、无需改 `route_registry.py` 与路由基线；缺省参数时行为与旧客户端完全一致。
- 备选 A：新增 `POST /api/sessions/{id}/archive`。否决——多一条路由与基线维护，且与既有置顶写法不一致。
- 备选 B：`DELETE` 复用为归档。否决——既有 `DELETE` 已是物理删除，语义冲突不可逆。

### D4. 侧边栏行结构改为「行容器 = 打开按钮 + 归档按钮」，恢复用弹窗
`renderSidebarRecentSessions()` 每条由单一按钮改为行容器：主按钮保持原有标题、激活态与点击打开行为，新增独立归档图标按钮（默认隐藏、悬停/`:focus-within` 显现，键盘 Tab 可达），点击 `event.stopPropagation()` 不触发打开。恢复入口放在「查看全部」旁，打开一个沿用 `confirm-overlay` 样式的归档会话弹窗（列表 + 每条「恢复」）。
- 理由：嵌套交互元素不能放在同一个 `<button>` 内，行容器是最小重构；弹窗复用既有模态样式，避免新增独立视图与路由。
- 备选 A：复用完整历史页承载归档视图。否决——与本次「仅侧边栏」范围和既有 `session-history-workbench` 入口迁移口径冲突，改动更大。
- 备选 B：右键/长按菜单。否决——移动端与可发现性差，用户诉求是「右侧按钮」。

### D5. 非破坏性：归档当前会话保持打开，不取消进行中运行
归档只置标记，不调用删除链路的取消/清理；当前会话被归档后对话视图继续可用，进行中回复照常完成。成功用轻提示反馈，失败回滚该行并提示原因。
- 理由：符合「归档可恢复、非删除」的语义；删除链路里的取消与队列清空会让归档等同于删除。
- 备选：归档即切换相邻/新建会话并取消运行。否决——超出用户诉求且易误伤正在生成的回复。

### D6. 归档保留置顶标记
归档只改 `archived`，不清 `pinned`；恢复后会话按 `ORDER BY pinned DESC, last_active DESC` 回到原有置顶分区。
- 理由：归档是隐藏而非重置用户偏好，恢复应还原原状。

### D7. 授权沿用读取权限与作用域校验
归档/恢复/归档列表均要求 `history.read` 并通过 `_require_session_scope(ctx, session_id, agent_id)`（或列表侧的 `_list_sessions_across_agents` 租户/用户过滤）解析目标智能体；写入以 `set_archived()` 的 `agent_id`/`tenant_id` 作用域限定，误传其他智能体的会话不会命中。
- 理由：与会话重命名/置顶的既有鉴权口径一致，不引入新的权限概念，也不扩大可见范围。

## Risks / Trade-offs

- [归档会话仍可能被其他入口打开或继续收到消息] → 归档只在列表过滤层面隐藏；按 D5 当前会话保持打开是刻意行为，消息继续持久化但不重新出现在历史，直到恢复。
- [`list_session_ids` 默认排除归档会改变项目空间计数，进而改变列表分组模式] → 这是期望结果：归档会话不应继续撑大空间数；若用户恢复会话，计数与分组会随之恢复。
- [跨智能体 `total` 的正确性] → 过滤在单个 store 查询内完成，`total` 与分页均基于过滤后集合，合并与去重逻辑不变。
- [既有库新增列失败导致历史不可用] → 使用既有 `plan_column_migrations()` 的 additive 路径，仅 `ALTER TABLE ADD COLUMN`；失败会被 `_migrate` 的既有错误处理捕获，不修改既有数据。
- [归档弹窗在会话很多时一屏放不下] → 首屏加载一页并提供可滚动列表与失败重试；本次不做归档内搜索/无限加载（见 Non-Goals）。
- [前端缓存旧资源看不到按钮] → 属正常前端发布行为，接口独立可用，不影响正确性。
- [并发归档/恢复] → 操作幂等（置 0/1），以最后一次写为准；失败回滚列表行并提示。

## Migration Plan

- 发布后端（新增列与接口）后再发布前端；`archived` 列由 `_init_db()` 自动迁移，既有行默认 0，无需回填或一次性批处理。
- 无 feature flag：纯新增列与可选参数，旧客户端不带 `archived` 时行为不变。
- 回滚：移除归档按钮/接口调用即可，`archived` 列保留且无副作用；不涉及数据删除或恢复脚本。

## Open Questions

无。
