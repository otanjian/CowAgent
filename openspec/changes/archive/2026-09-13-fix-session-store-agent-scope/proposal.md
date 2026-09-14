## Why

在 database 身份模式下，侧边栏「会话历史」重命名/归档会话时后端返回 `session not found`（实测：`session_bf7bb5bb-…` 存在于 `/Users/jiantan/cow/agents/my-assistant-admin/memory/long-term/index.db`，但处理器去 `/Users/jiantan/cow/memory/long-term/index.db` 找它）。

原因：会话是**按 Agent 工作区分库**存储的（`<workspace>/memory/long-term/index.db`），列表与归属校验都按 Agent 工作区读；但所有按会话 ID 的写处理器用 `get_conversation_store(_get_workspace_root(agent_id=...))` 解析存储。`_get_workspace_root` 在 database 模式下走 `resolve_tenant_workspace_root()`，返回**租户共享根**并忽略 `agent_id`，于是写操作查的是另一个库 → `rename_session`/`set_archived`/删除均命中不到行。

影响面不止重命名：会话删除、置顶、标题生成回填、清空上下文、删除单条消息，以及单 Agent 分支的历史列表，全部寻址到错误的库。已归档功能（`PUT archived`）同样受此影响。

## What Changes

- 新增一个会话存储解析入口：按**被寻址 Agent 的工作区**打开会话存储，与历史列表合并（`_list_sessions_across_agents`）和归属校验（`_require_owned_session`）使用同一个库。
- 所有按会话 ID 的读写（单 Agent 历史列表、重命名/置顶/归档、会话删除、标题回填、清空上下文、删除单条消息）SHALL 改用该入口，MUST NOT 再用工作区根解析会话存储。
- 文件面板/预览/上传/知识库等**租户工作区**语义保持不变，仍按租户共享根解析。
- 补齐会让该缺陷漏过的测试：不再 stub `_get_workspace_root`，并新增「写处理器与列表指向同一个库」的断言。

## Capabilities

### New Capabilities
<!-- 无新增能力 -->

### Modified Capabilities
- `session-history-workbench`: 明确会话作用域的读写 SHALL 指向被寻址 Agent 的会话存储，与历史列表可读到的集合一致；工作区根解析 MUST NOT 被用于定位会话存储。

## Impact

- 后端：`channel/web/web_channel.py`（新增会话存储解析入口；替换 6 处按会话 ID 的 `get_conversation_store(_get_workspace_root(...))`）。
- 测试：新增存储解析回归/一致性用例；修正 `tests/test_session_archive.py`、`tests/test_session_history_search.py` 中把 `_get_workspace_root` 当作会话存储解析器的 stub；更新 `tests/test_agent_web_management.py` 中固化旧写法的源码断言。
- 不改动：租户工作区/文件面板/上传/知识库的解析、会话 schema、路由与鉴权口径。
