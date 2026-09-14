## Why

侧边栏「会话历史」的条目目前只能单击打开：想改标题必须先进入该会话、再切到完整「历史会话」页用「更多操作 → 重命名」，路径过长。完整历史页已经支持行内重命名，侧边栏缺少同等能力，用户希望直接在侧边栏双击标题就地改名。

## What Changes

- 侧边栏「会话历史」每条会话标题 SHALL 支持双击进入行内编辑，编辑框预填当前标题。
- 保存/取消键位与完整历史页一致：Enter 保存、Escape 取消、失焦保存；空白或未改动的标题 MUST NOT 发出写入请求。
- 保存成功静默（不弹提示）；写入失败 SHALL 回滚该条标题并说明原因；无权限或不存在时 MUST NOT 改动本地标题。
- 键盘可达：聚焦某条会话时按 F2 SHALL 进入同一行内编辑。
- 单击打开会话的既有行为 MUST NOT 改变，MUST NOT 为双击引入打开延迟；进入编辑 MUST NOT 触发打开或切换会话。
- 复用既有 `PUT /api/sessions/{id}` 的 `title` 与 `history.read` 授权，不新增路由、不改数据模型。

## Capabilities

### New Capabilities
<!-- 无新增能力 -->

### Modified Capabilities
- `session-history-workbench`: 侧边栏历史条目增加双击/ F2 行内重命名，明确键位、失焦行为、静默成功、失败回滚与键盘可达，并保持单击打开不变。

## Impact

- 前端：`channel/web/static/js/console.js`（侧边栏行内编辑动作）、`channel/web/static/css/console.css`（侧边栏行内编辑框样式，浅/深主题）。
- 测试：新增侧边栏重命名前端用例；不新增后端行为，`tests/fixtures/console_i18n_snapshot.json` 无新增文案（复用既有 `rename_session` / `session_settings_failed`）。
- 不改动：会话存储与 schema、HTTP 接口、归档/删除/置顶语义、完整历史页既有重命名路径。
