## 1. 数据模型与迁移

- [x] 1.1 `agent/memory/conversation_schema.py` 的 `_HISTORICAL_TABLES[SESSIONS]` 增加可加列 `archived INTEGER NOT NULL DEFAULT 0`
- [x] 1.2 确认既有库升级后 `archived` 全为 0（未归档），新建库与升级库 schema 一致，无回填脚本

## 2. 存储层

- [x] 2.1 `agent/memory/conversation_store.py` 的 `list_sessions(..., archived: bool = False)` 增加 `archived = ?` 过滤，与 `q`/`channel_type`/`owner`/作用域叠加，默认只返回未归档
- [x] 2.2 `list_session_ids(..., archived: bool = False)` 默认排除归档会话，保持项目空间计数正确
- [x] 2.3 新增 `set_archived(session_id, archived) -> bool`，作用域与 `set_pinned` 一致（`agent_id`/`tenant_id`），仅更新标记、不清置顶
- [x] 2.4 `list_sessions` 返回值与排序（`pinned DESC, last_active DESC`）在过滤后保持不变，`total`/`has_more` 基于过滤后集合

## 3. HTTP 接口

- [x] 3.1 `channel/web/web_channel.py` 的 `SessionDetailHandler.PUT` 接受 `archived` 布尔（与 `title`/`pinned` 同路），调用 `store.set_archived`；`title`/`pinned`/`archived` 全缺时返回参数错误
- [x] 3.2 `SessionsHandler.GET` 支持 `archived` 查询参数并透传 `_list_sessions_across_agents` 与单 store 分支
- [x] 3.3 `_list_sessions_across_agents` 增加 `archived` 形参并透传各 store 的 `list_sessions`
- [x] 3.4 归档/恢复/归档列表沿用 `_require_read_permission(ctx, "history.read")` 与 `_require_session_scope`；缺省 `archived` 时旧客户端行为不变，路由与 `scripts/route-baseline.txt` 无需改动

## 4. 前端

- [x] 4.1 `console.js` 的 `renderSidebarRecentSessions()` 每条改为行容器：主打开按钮 + 独立归档按钮（`stopPropagation`，不触发打开）
- [x] 4.2 实现 `archiveSidebarSession(sessionId, agentId)`：`PUT /api/sessions/{id}` 置 `archived=true`，成功后从 `_sidebarRecentItems` 移除并重绘、提示已归档；失败回滚该行并提示原因
- [x] 4.3 侧边栏「查看全部」旁新增「已归档」入口与归档会话弹窗：拉取 `GET /api/sessions?scope=all&archived=1`，区分加载中/空态/失败重试，每条提供恢复
- [x] 4.4 实现 `restoreArchivedSession(sessionId, agentId)`：`PUT {archived:false}`，成功后从弹窗移除并刷新侧边栏历史
- [x] 4.5 `console.css` 增加行内归档按钮（悬停/`:focus-within` 显现、键盘可达、不遮挡标题）与归档弹窗样式，浅/深主题均可读
- [x] 4.6 `channel/web/static/js/i18n/core.js` 增加 zh / zh-Hant / en 文案（归档、已归档、恢复、成功提示、失败提示、空态）

## 5. 测试与校验

- [x] 5.1 新增/扩展 Python 用例：归档后默认列表与 `total` 隐藏、`archived=True` 可读、`set_archived` 置顶保留、作用域隔离、`list_session_ids` 过滤、`PUT` 与 `GET` 的 `archived` 参数
- [x] 5.2 新增前端 `.cjs` 用例：侧边栏渲染归档按钮、点击调用接口并移除该行、不触发 `switchSession`、归档弹窗渲染与恢复刷新
- [x] 5.3 更新 `tests/fixtures/console_i18n_snapshot.json`（新增文案）
- [x] 5.4 运行相关 pytest 与 `.cjs` 测试并确认通过
- [x] 5.5 `openspec validate add-sidebar-session-archive --strict` 通过

## 6. 收尾

- [x] 6.1 确认无 feature flag 需求与旧客户端兼容（缺省参数行为不变），回滚只需移除前端入口与接口调用
- [x] 6.2 若实现与 spec 出现偏差，同步修订 delta spec 与 design 后再归档
