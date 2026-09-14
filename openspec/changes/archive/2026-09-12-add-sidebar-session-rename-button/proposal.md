## Why

侧边栏「会话历史」已支持双击（或聚焦后按 F2）重命名标题，但入口不可见：用户必须先知道有这个手势才能用，鼠标用户也容易误触为「打开两次」。归档按钮已经以悬停显现的图标形式给出了可发现的逐条操作位置，重命名应获得同等、并列的可见入口。

## What Changes

- 侧边栏「会话历史」每条会话 SHALL 在归档按钮旁提供独立的「重命名」按钮，点击进入与双击相同的行内编辑。
- 该按钮 SHALL 与归档按钮采用同样的呈现与可达性：图标形式、悬停或行内聚焦时显现、触摸设备常显、键盘 Tab 可达，且 MUST NOT 遮挡标题。
- 点击重命名按钮 MUST NOT 打开或切换该会话，MUST NOT 触发归档。
- 既有双击与 F2 入口 MUST 保留，键位、失焦保存、静默成功与失败回滚语义均不变。
- 复用既有 `rename_session` 与 `session_settings_failed` 文案，不新增接口与文案。

## Capabilities

### New Capabilities
<!-- 无新增能力 -->

### Modified Capabilities
- `session-history-workbench`: 侧边栏历史条目的行内重命名在双击 / F2 之外增加并列可见的重命名按钮入口，明确其与归档按钮并列的呈现、可达性与不误开会话要求。

## Impact

- 前端：`channel/web/static/js/console.js`（`renderSidebarRecentSessions()` 增加重命名按钮并绑定 `renameSidebarSession`）、`channel/web/static/css/console.css`（按钮样式与悬停/聚焦显现规则）。
- 测试：扩展 `tests/test_sidebar_session_rename_frontend.cjs`；不新增后端行为，`tests/fixtures/console_i18n_snapshot.json` 无新增文案。
- 不改动：会话存储与 schema、HTTP 接口、归档/删除/置顶语义、完整历史页既有重命名路径。
