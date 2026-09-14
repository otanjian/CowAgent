## 1. 前端按钮

- [x] 1.1 `console.js` 的 `renderSidebarRecentSessions()` 在打开按钮与归档按钮之间新增 `button.sidebar-recent-rename-btn`（`type=button`、`fa-pen` 图标、`aria-label` 用既有 `rename_session` 拼接标题、`title` 用 `rename_session`），`click` 时 `stopPropagation()` 并调用 `renameSidebarSession(session_id, ownerId)`
- [x] 1.2 确认双击与 F2 入口保持不变，且三个触发入口共用同一 `renameSidebarSession()`

## 2. 样式

- [x] 2.1 `console.css` 增加 `.sidebar-recent-rename-btn`，复用归档按钮的尺寸/圆角/悬停色
- [x] 2.2 把重命名按钮加入 `.sidebar-recent-row:hover` / `:focus-within` 显现选择器与 `@media (hover: none)` 常显规则，确保两枚按钮并列不遮挡标题

## 3. 测试与校验

- [x] 3.1 扩展 `tests/test_sidebar_session_rename_frontend.cjs`：每行存在与归档并列的重命名按钮；点击进入编辑并预填标题；不触发 `switchSession`；不触发归档（无 DELETE/PUT archived 请求）；双击与 F2 仍可用
- [x] 3.2 确认无需改动 `tests/fixtures/console_i18n_snapshot.json`
- [x] 3.3 运行重命名与归档前端用例、i18n parity 用例并确认通过
- [x] 3.4 `openspec validate add-sidebar-session-rename-button --strict` 通过

## 4. 收尾

- [x] 4.1 确认无后端改动、无数据迁移，回滚只需移除按钮渲染与样式
- [x] 4.2 若实现与 spec 出现偏差，同步修订 delta spec 与 design 后再归档
