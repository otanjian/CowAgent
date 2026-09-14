## Context

现状（动机见 proposal.md - Why，需求见 `specs/session-history-workbench/spec.md`）：

- 侧边栏「会话历史」由 `console.js` 的 `renderSidebarRecentSessions()` 渲染，每条为一个行容器 `.sidebar-recent-row`，内含打开按钮 `.sidebar-recent-item`（单击 `switchSession`）与归档按钮 `.sidebar-recent-archive-btn`（`stopPropagation`）。
- 完整「历史会话」页已有行内重命名 `renameSession(sid, agentId)`：把 `.session-title` 换成 `input.session-title-input`，Enter 保存 / Escape 取消 / blur 保存，乐观更新并写 `PUT /api/sessions/{id}`（body `{title, agent_id}`），成功后 `_refreshHistoryList()`，失败回滚 `_sessionItems` 缓存与 DOM 并 `_wsToast`。
- `switchSession()` 不重绘侧边栏，只对既有 `.sidebar-recent-item` 切换 `.active` 类，因此双击事件仍落在同一行节点上。
- 标题写入接口与授权已存在，无需后端改动。

## Goals / Non-Goals

**Goals:**
- 在侧边栏历史条目上提供与完整历史页一致的行内重命名体验（键位、失焦、静默成功、失败回滚）。
- 保持单击打开不变，不为区分双击引入任何打开延迟。
- 键盘可达：双击之外提供 F2 入口。

**Non-Goals:**
- 不改会话存储、schema 与 HTTP 接口；不新增路由。
- 不改完整历史页既有重命名路径与「更多操作」菜单。
- 不在归档弹窗内提供重命名；不做批量重命名、标题历史或重名校验。

## Decisions

### D1. 复用 `PUT /api/sessions/{id}` 的 `title`，零后端改动
行内编辑提交沿用完整历史页同一接口与 `history.read` 授权，body 为 `{title, agent_id}`，作用域随 `agent_id` 限定。
- 理由：重命名语义与授权口径已存在且被测试覆盖；新增端点只会扩大维护面与路由基线。
- 备选：新增 `PATCH /api/sessions/{id}/title`。否决——与既有 `title` 写入重复。

### D2. 在侧边栏行内就地编辑，隐藏主按钮而非嵌套
进入编辑时给主按钮加 `hidden` 类并 `insertBefore` 一个 `input.sidebar-recent-rename-input`，退出时移除输入框并恢复按钮。
- 理由：`input` 不能合法嵌套在 `<button>` 内，且会误触按钮的打开点击；兄弟节点插入是最小重构，与历史页换出 `.session-title` 的做法同构。
- 备选：把按钮内容整体换成输入框。否决——交互式元素嵌套在 button 内属无效结构且点击会冒泡到打开逻辑。

### D3. 键位与完整历史页对齐
Enter 保存（忽略 IME 组合中的 Enter 与 `keyCode 229`）、Escape 取消、blur 保存；空白或与原标题一致时不发请求直接退出。用 `done` 标志防止 Enter/blur 重复提交。
- 理由：同一产品内两处重命名行为一致，降低学习成本；`done` 与历史页 `restore/commit` 的守卫方式相同。
- 备选：仅 Enter 保存、blur 取消。否决——用户已选择与历史页一致。

### D4. 成功静默、失败回滚 + 提示
成功不弹提示；失败时把 DOM 与 `_sidebarRecentItems` 缓存中的标题一并回滚为编辑前的值并 `_wsToast(message || t('session_settings_failed'))`。
- 理由：重命名是高频轻操作，成功提示是噪音；失败必须让用户知道写入没生效。
- 备选：成功也提示。否决——用户已选择静默。

### D5. 单击打开不变，双击不引入延迟
保持 `.sidebar-recent-item` 的单击 `switchSession`；`dblclick` 与 F2 都调用 `renameSidebarSession()`。由于 `switchSession` 不重绘侧边栏，双击的两次 click 只做幂等切换（第一次切到该会话，第二次命中 `newSessionId === sessionId` 直接返回），随后 `dblclick` 正常进入编辑。
- 理由：给单击加 200–300ms 判定窗口会拖慢最高频的打开操作；双击改名以「该会话成为当前会话」为前提是可接受且可预期的副作用。
- 备选：延迟单击判定以区分双击。否决——牺牲打开体验换取的收益极小。

### D6. 不新增文案，复用既有 i18n
编辑框 `aria-label` 用既有 `rename_session`，失败提示用既有 `session_settings_failed`。
- 理由：文案已在三语文案表中存在且语义精确，无需改动 `tests/fixtures/console_i18n_snapshot.json`。
- 备选：新增「已重命名」等键。否决——成功静默，无文案需求。

## Risks / Trade-offs

- [双击会先把该会话变为当前会话] → 刻意取舍（见 D5）；单击打开语义不变，双击后用户本就聚焦该条。
- [编辑期间侧边栏被刷新会丢失编辑] → 刷新只在归档/恢复与显式加载后发生；编辑中不会触发这些动作，且 blur 会先提交。
- [与归档按钮的交互冲突] → 归档按钮独立且 `stopPropagation`；编辑态只隐藏主按钮，归档按钮保持可用。
- [移动端无双击语义] → 移动端沿用单击打开；重命名仍可在完整历史页完成，本次不为触摸新增长按等手势（见 Non-Goals）。

## Migration Plan

- 纯前端改动，无数据迁移、无 feature flag；后端与接口不变，旧客户端不受影响。
- 回滚：移除侧边栏双击/F2 绑定即可，完整历史页重命名不受影响。

## Open Questions

无。
