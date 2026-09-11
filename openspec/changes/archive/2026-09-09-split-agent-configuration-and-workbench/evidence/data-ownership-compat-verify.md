# 数据归属与兼容边界核验（5.1）

## 结论

实施前后 Agent ID、配置及会话数据归属**一致**；新旧前后端接口兼容；仅回退本 change 代码即可恢复，**不删除任何已生成的用户会话**。

## 1. 数据归属一致

- 未修改 `agent/admin.py` 的 `AgentAdminService` 数据模型；未新增 Agent ID；未迁移会话数据库；未复制配置存储。
- 工作台投影与默认读取**共用同一数据源**（`AgentRegistry.list()` / `AgentAdminService.snapshot()`），仅做字段白名单裁剪，无新数据归属（详见 `evidence/data-ownership-baseline.md`）。

## 2. 兼容边界（向后 + 向前）

`AgentsHandler.GET` 以 `view` 参数分派：

```python
params = web.input(view='')
if params.view == 'workbench':
    return json.dumps({"status": "success", **_workbench_agents_projection()}, ...)
return json.dumps({"status": "success", **_agent_admin_service().snapshot()}, ...)
```

- **无参数 / 其他 view**：返回完整管理快照（含 `default_agent_id`、`agents`、`channel_instances`、`revision`），与 `test_default_view_returns_full_snapshot` 断言一致，保持 Desktop `RosterSnapshot` 契约。
- **`view=workbench`**：返回白名单投影（`id`、`name`、`description`、`avatar`、`is_default`、`can_chat`、`unavailable_reason`），不含 `workspace`、`channel_instances`、`revision`。

## 3. 恢复方式（仅回退本 change）

本 change 新增且可单独回退的项：
- `channel/web/web_channel.py`：`_workbench_agents_projection`、`_workbench_chat_readiness`、`AgentsHandler.GET` 中的 `view=workbench` 分支。
- `channel/web/static/js/console.js`：`loadAgentWorkbench`、`renderAgentWorkbench`、`agentWorkbenchCardHTML`、`setWbStatus`、`setWbError`、`refreshWorkbenchAfterUnavailable`、`paintChatAgentIdentity`、`focusChatComposer`，以及 `startChatWithAgent` 中新增的保护流程、`switchSession(newSessionId, agentId)` 签名、`VIEW_META` 与导航改动。
- `channel/web/chat.html`：`view-agent-workbench` 容器、`chat-agent-identity`、侧栏 `menu_agent_config` 项。
- `channel/web/static/css/console.css`：`.agent-workbench-*`、`.chat-agent-identity`、`.agent-wb-retry` 等样式。
- `tests/test_agent_workbench.py`（新增）。

**未触碰**：会话删除/清理逻辑（diff 中无 `delete_session` / `clear_session` / `forget_session` 新增），Agent 数据模型，会话数据库。回退上述代码不影响已生成会话。

## 4. 与其它 in-progress change 的边界

共享工作树中还包含其它未提交 change 的改动（branding、tenant identity、auth、todos 等）。它们与本 change 独立：
- `test_branding.py` / `test_openai_chat_api.py` 的组合失败源于**模块级 `web` stub 污染**（各自独立运行时全绿，见 4.5 记录），属这些 change 的隔离问题，与本 change 无关。
- 本 change 的任务状态保持独立（见 tasks.md），不改变其它 change 的 `*.openspec.yaml` / 任务进度。
