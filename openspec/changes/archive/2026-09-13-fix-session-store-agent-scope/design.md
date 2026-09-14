## Context

症状与证据（动机见 proposal.md - Why）：

- 会话存放于 `<agent_workspace>/memory/long-term/index.db`（`agent/memory/conversation_store.py::_resolve_store_path`）。
- 列表侧正确：`_list_sessions_across_agents()` 对每个 Agent 用 `get_conversation_store(profile.workspace)`；`_require_owned_session()` 也用 `profile.workspace`。
- 写侧错误：`SessionDetailHandler.PUT/DELETE`、`SessionTitleHandler.POST`、`SessionClearContextHandler.POST`、`MessageDeleteHandler.POST`，以及 `SessionsHandler.GET` 的单 Agent 分支，都用 `get_conversation_store(_get_workspace_root(agent_id=agent_id))`。
- `_get_workspace_root()` 在 database 模式下命中 fork 接缝 `resolve_tenant_workspace_root()`，返回**租户共享根**并忽略 `agent_id`（该函数是文件面板/预览/租户边界的语义，保持不变）。
- 实测：`session_bf7bb5bb-…` 在 `/Users/jiantan/cow/agents/my-assistant-admin/memory/long-term/index.db`，而处理器解析出 `/Users/jiantan/cow/memory/long-term/index.db`，`rename_session` 返回 False → `session not found`。
- 既有测试为何没发现：`tests/test_session_archive.py` 与 `tests/test_session_history_search.py` 直接 stub 了 `_get_workspace_root` 让它返回 agent id（把错误解析器当成了正确解析器），`tests/test_agent_web_management.py` 甚至把错误写法写成了源码断言。

## Goals / Non-Goals

**Goals:**
- 会话作用域的读与写指向同一个（被寻址 Agent 的）存储。
- 一处定义解析规则，避免再次各写一遍。
- 让回归测试无法再用 stub 掩盖解析错误。

**Non-Goals:**
- 不改 `_get_workspace_root` 与 `resolve_tenant_workspace_root`（文件面板/预览/知识库的租户边界必须保持）。
- 不做跨库会话搬迁或历史数据迁移。
- 不改会话 schema、路由、鉴权口径与前端。

## Decisions

### D1. 新增 `_conversation_store_for(agent_id)` 作为唯一会话存储解析入口
```python
def _conversation_store_for(agent_id):
    """被寻址 Agent 的会话存储。"""
    from agent.memory import get_conversation_store
    from agent.registry import get_agent_registry
    return get_conversation_store(get_agent_registry().get(agent_id or None).workspace)
```
调用方一律先经 `_require_session_scope()` 取得已解析的 agent id 再调用它，因此归属校验与存储解析用的是同一个 Agent。
- 理由：与 `_list_sessions_across_agents`、`_require_owned_session` 的既有归属口径完全一致；不需要改动租户工作区语义。
- 备选 A：直接在每个处理器内联 `get_agent_registry().get(agent_id).workspace`。否决——六处重复，正是本缺陷的成因。
- 备选 B：改 `_get_workspace_root` 在传入 `agent_id` 时返回 Agent 工作区。否决——该函数同时服务文件面板/预览，会破坏租户共享根语义。

### D2. 替换全部按会话 ID 的会话存储解析
`SessionsHandler.GET` 单 Agent 分支、`SessionDetailHandler.PUT`、`SessionDetailHandler.DELETE`、`SessionTitleHandler.POST`、`SessionClearContextHandler.POST`、`MessageDeleteHandler.POST` 改调 `_conversation_store_for(agent_id)`。
- 理由：读一次写一次若指向不同库，就会出现「列表可见但操作报不存在」这类自相矛盾状态；必须一起改。
- 备选：只修 PUT（用户报告的那条）。否决——DELETE/置顶/清空上下文/删单条消息仍在错误库上操作，属同类缺陷。

### D3. 测试改为走真实解析，并断言读写一致
1. 去掉 `_get_workspace_root` 的会话存储 stub；
2. 新增 HTTP 级用例：在**非默认** Agent 的存储中放置会话，重命名/归档/删除成功且写入该存储；
3. 新增一致性断言：对同一 Agent，写路径与列表路径传给 `get_conversation_store` 的工作区相同。
- 理由：原缺陷是被 stub 掩盖的，测试必须覆盖真实解析路径才算修好；一致性断言能抓住「只改了其中一条路径」。
- 备选：只补一个值断言（改完标题等于新值）。否决——无法防回归到 `_get_workspace_root`。

## Risks / Trade-offs

- [单 Agent 分支列表内容会变化] → 该分支此前读的是租户共享根（既非默认 Agent 的工作区），修正后返回被寻址 Agent 的真实会话；这是修复而非行为回退，且与 `scope=all` 合并分支从此一致。
- [`get_agent_registry().get(agent_id)` 对未知 id 抛错] → 调用前已由 `_require_session_scope()` → `_require_tenant_agent_binding()` 校验绑定，未知 Agent 在此之前已 404。
- [历史遗留：租户共享根库里还留着旧会话] → 本次不迁移（见 Non-Goals）；它们本就未出现在按 Agent 合并的列表中。
- [既有 stub 测试变得无意义] → 显式清理，避免「假绿」。

## Migration Plan

- 纯后端解析修正，无数据迁移、无 feature flag。
- 回滚：还原解析调用即可；两库都未被改写。
- 发布顺序无关：不涉及前端与接口契约。

## Open Questions

无。
