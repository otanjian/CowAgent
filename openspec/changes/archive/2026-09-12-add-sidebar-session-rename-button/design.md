## Context

现状（动机见 proposal.md - Why，需求见 `specs/session-history-workbench/spec.md`）：

- `renderSidebarRecentSessions()` 每条渲染为 `.sidebar-recent-row`，内含打开按钮 `.sidebar-recent-item` 与归档按钮 `.sidebar-recent-archive-btn`（图标按钮，`opacity: 0` 默认隐藏，`.sidebar-recent-row:hover` / `:focus-within` 显现，`@media (hover: none)` 常显）。
- 行内重命名已实现为 `renameSidebarSession(sessionId, agentId)`，由打开按钮的 `dblclick` 与 `keydown` F2 调用。

## Goals / Non-Goals

**Goals:**
- 给行内重命名一个与归档按钮并列、可发现的可见入口。
- 与归档按钮共享同一呈现语言与可达性规则，保持侧边栏视觉一致。

**Non-Goals:**
- 不改重命名本身的键位、失焦、静默成功与失败回滚语义。
- 不新增图标库依赖、接口、数据模型或文案。
- 不改完整历史页与归档弹窗的操作入口。

## Decisions

### D1. 复用 `renameSidebarSession()`，只加一个触发按钮
新按钮的 `click` 直接调用现有函数并 `stopPropagation()`，不复制编辑逻辑。
- 理由：编辑、提交与回滚语义已实现并被测试覆盖；按钮只是第三个触发入口（双击、F2、按钮）。
- 备选：为新按钮另写一套编辑逻辑。否决——会产生两份需同步维护的提交/回滚路径。

### D2. 图标按钮 + 与归档一致的显现规则
使用 `<i class="fas fa-pen">`（`fa-pen` 已在完整历史页「更多操作 → 重命名」使用），`aria-label` 为 `t('rename_session') + ': ' + title`，`title` 为 `t('rename_session')`。样式复用归档按钮的尺寸、圆角与悬停色，并把它加入既有的 hover/`:focus-within` 显现选择器与 `@media (hover: none)` 常显规则。
- 理由：并列的两个逐条操作应可预测且视觉等权；文本按钮会挤压侧边栏标题空间。
- 备选 A：文字按钮「修改」。否决——侧边栏宽度有限且与图标式归档按钮不对称。
- 备选 B：常显图标按钮。否决——两条常显图标会持续压缩标题可读宽度，与既有归档按钮的显现约定不一致。

### D3. 按钮顺序为「重命名 → 归档」，放在打开按钮之后
行内顺序：打开按钮、重命名按钮、归档按钮。重命名是低破坏性的修改操作，归档是隐藏操作，按破坏性递增排列。
- 理由：符合从左到右破坏性递增的常见约定，也与完整历史页「重命名在删除之前」一致。

## Risks / Trade-offs

- [两个图标按钮使标题可用宽度变小] → 两枚按钮宽度固定（各 24px）且仅在悬停/聚焦时显现，未悬停时标题保持完整宽度；标题本身仍是 `text-overflow: ellipsis`。
- [触摸设备上两枚按钮常显] → 明确接受（见 Non-Goals），触摸下两者都可点且不遮挡标题；重命名在触摸上也可由按钮进入，反而优于只能依赖双击。
- [误点归档] → 两枚按钮互不相连且各自 `stopPropagation`，归档仍需单独点击。

## Migration Plan

- 纯前端改动，无数据迁移、无 feature flag；接口与后端不变。
- 回滚：移除该按钮的渲染与样式即可，双击 / F2 入口不受影响。

## Open Questions

无。
