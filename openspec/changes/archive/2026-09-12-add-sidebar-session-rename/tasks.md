## 1. 前端行内编辑

- [x] 1.1 `console.js` 的 `renderSidebarRecentSessions()` 为主按钮绑定 `dblclick`（`preventDefault` + `stopPropagation`）与 `keydown` F2，二者均调用 `renameSidebarSession(session_id, agentId)`，不改变既有单击 `switchSession`
- [x] 1.2 实现 `renameSidebarSession(sessionId, agentId)`：定位 `.sidebar-recent-row`，隐藏主按钮并以 `insertBefore` 插入 `input.sidebar-recent-rename-input`（预填并选中当前标题、`maxLength=100`、`aria-label` 用既有 `rename_session`）
- [x] 1.3 提交语义与历史页对齐：Enter 保存（忽略组合输入与 `keyCode 229`）、Escape 取消不写入、blur 保存、空白或未改动直接退出；`done` 标志防重复提交
- [x] 1.4 编辑期间屏蔽打开：输入框 `click`/`mousedown` `stopPropagation`
- [x] 1.5 提交走 `PUT /api/sessions/{id}?agent_id=...`，body `{title, agent_id}`；乐观更新 DOM 与 `_sidebarRecentItems` 缓存，成功静默，失败回滚两处标题并 `_wsToast(message || t('session_settings_failed'))`

## 2. 样式

- [x] 2.1 `console.css` 增加 `.sidebar-recent-rename-input`（浅/深主题可读、与行高对齐、不遮挡归档按钮）与主按钮隐藏态 `.sidebar-recent-item.hidden`

## 3. 测试与校验

- [x] 3.1 新增前端 `.cjs` 用例：双击进入编辑且预填标题、不触发 `switchSession`；Enter 提交写入并静默；Escape 取消不写入；blur 提交；空白/未改动不写入；失败回滚标题并提示原因；F2 进入编辑
- [x] 3.2 确认无需改动 `tests/fixtures/console_i18n_snapshot.json`（复用既有键）
- [x] 3.3 运行新增 `.cjs` 用例、既有侧边栏归档用例与 i18n parity 用例并确认通过
- [x] 3.4 `openspec validate add-sidebar-session-rename --strict` 通过

## 4. 收尾

- [x] 4.1 确认无后端改动、无数据迁移，回滚只需移除侧边栏双击/F2 绑定
- [x] 4.2 若实现与 spec 出现偏差，同步修订 delta spec 与 design 后再归档
