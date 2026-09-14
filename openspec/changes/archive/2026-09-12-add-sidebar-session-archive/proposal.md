## Why

侧边栏「会话历史」会一直累积所有最近会话，用户无法把已经处理完、不想再看到的会话从日常视图中收起；而现有唯一手段是「删除」，会连同消息一起物理清除，代价过高且不可恢复。需要一种可逆的收起方式：从侧边栏历史中移除该会话，同时完整保留其消息与归属，并可在「已归档」中找回。

## What Changes

- 侧边栏「会话历史」每条会话右侧新增「归档」按钮（悬停/键盘聚焦时可见），点击后该会话从侧边栏历史中消失，并提示「已归档」。
- 归档为**软归档**：会话消息、标题、智能体归属、项目绑定与置顶标记全部保留，MUST NOT 物理删除。
- 归档仅作用于侧边栏入口：完整「历史会话」页与侧边栏列表默认都不再返回已归档会话；当前正在查看的会话被归档后，对话本身保持打开、不切换、不取消进行中的回复。
- 侧边栏「会话历史」新增「已归档」入口，打开归档会话弹窗；弹窗列出归档会话并提供「恢复」，恢复后重新出现在普通历史中。
- 会话存储新增 `archived` 可加列与归档/恢复写入；列表查询默认按 `archived=0` 过滤，`archived=1` 用于归档视图。接口 `PUT /api/sessions/{id}` 支持 `archived` 布尔，`GET /api/sessions` 支持 `archived` 查询参数。
- 归档/恢复沿用既有 `history.read` 与目标智能体作用域校验，未授权会话 MUST NOT 被归档、恢复或出现在归档视图。
- 归档不要求二次确认；失败时恢复该行并提示原因。

## Capabilities

### New Capabilities
<!-- 无新增能力 -->

### Modified Capabilities
- `session-history-workbench`: 在侧边栏历史列表增加逐条归档（软归档、从历史隐藏、可恢复）；新增「已归档」查看与恢复入口；明确归档默认过滤、归档不破坏当前对话与进行中运行、失败与作用域边界。

## Impact

- 后端：`agent/memory/conversation_schema.py`（`sessions` 新增可加列 `archived`）、`agent/memory/conversation_store.py`（`list_sessions` / `list_session_ids` 增加归档过滤，新增 `set_archived`）、`channel/web/web_channel.py`（`SessionsHandler.GET` 支持 `archived`，`SessionDetailHandler.PUT` 支持 `archived`）。
- 前端：`channel/web/static/js/console.js`（侧边栏行结构与归档按钮、归档/恢复动作、归档弹窗）、`channel/web/static/css/console.css`（行内归档按钮与归档弹窗样式）、`channel/web/static/js/i18n/core.js`（zh / zh-Hant / en 文案）。
- 测试：新增归档 store / 接口 / 前端用例；更新 `tests/fixtures/console_i18n_snapshot.json`。
- 数据：仅新增默认 0 的可加列，既有行自动视为未归档，行为不变；回滚只需移除代码，列无副作用。
- 不改动：会话 ID、消息、项目绑定、置顶语义、完整历史页的删除/重命名/置顶行为，其他能力规范。
