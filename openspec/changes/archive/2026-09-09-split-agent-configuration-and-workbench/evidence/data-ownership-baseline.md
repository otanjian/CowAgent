# 阶段 A：Agent ID / 配置 / 会话 / 工作空间基线

## 1. Agent ID 与配置版本（revision）归属

- Agent 配置由 `agent/admin.py` 的 `AgentAdminService` 管理，存储于 `<data_root>/config.json`（`get_data_root()` 拼接），底层为 `team.json`（经 `team.resolve`/`team.write`）。
- `AgentAdminService.snapshot()` 返回：
  ```json
  {
    "default_agent_id": "...",
    "agents": [ {AgentProfile.to_dict()} ],
    "channel_instances": [],
    "revision": "..."
  }
  ```
- `AgentProfile.to_dict()`（`agent/registry.py`）字段白名单：`id`、`name`、`workspace`、`enabled`，以及可选 `description`、`model`、`bot_type`、`avatar`、`skills`、`knowledge`。`revision` 来自 `_roster_revision(settings)`，用于管理写入时的版本冲突（`StaleRosterError` → HTTP 409 `code: "stale_roster"`）。

## 2. 会话（session）归属

- 会话按 Agent 分库存储，见 `channel/web/web_channel.py` 注释「Sessions 是每个 Agent 一个数据库，因此『所有会话』会把花名册变成租户选择器」（约 7243-7250）。
- 会话 key：前端 `activeSessionStorageKey()` 在非默认 Agent 下用 `${SESSION_ID_KEY}:${activeAgentId}`；请求统一经 `window.fetch` 注入 `agent_id`。
- `/api/agents` 默认 GET 的响应为管理快照（含 `workspace`、`channel_instances`），供 `loadAgentCatalog()` 与 Desktop 默认目录使用。

## 3. 工作空间（workspace）归属

- 每个 Agent 有独立 `workspace`（绝对路径）。`resetWorkspaceToAgentRoot()` 前端在切换智能体入口调用，重置到目标 Agent 根工作空间。
- `_get_workspace_root(session_id, agent_id)` 在 Web 服务内解析到 `get_agent_registry().get(agent_id).workspace`。

## 4. 无需业务数据迁移的结论

- 本 change 仅新增 Web 侧「工作台使用页」与「`view=workbench` 读取投影」，不修改 Agent 配置结构、不新增 Agent ID、不迁移会话数据库、不复制配置存储。
- 工作台投影读取同一 `AgentAdminService.snapshot()` / `AgentRegistry.list()` 数据源，仅做字段白名单裁剪，无新数据归属。

## 5. 前端 Agent ID / 活动智能体引用基线

- `activeAgentId` 由 `localStorage.getItem('cow_active_agent')` 初始化（`console.js` 3876）。
- `loadAgentCatalog()` 内：若当前 `activeAgentId` 不在启用列表，回退到 `defaultAgentId` 并写回 localStorage。该副作用确认了「工作台加载不能复用 `loadAgentCatalog()`」的约束。
