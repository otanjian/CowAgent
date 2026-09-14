## 1. 失败测试（RED）

- [x] 1.1 新增 `tests/test_session_store_resolution.py`：在**非默认** Agent 的存储中放置会话，`PUT /api/sessions/{id}` 重命名返回 success 且写入该存储（不 stub `_get_workspace_root`）
- [x] 1.2 同文件：`PUT {archived:true}` 与 `DELETE /api/sessions/{id}` 同样作用于该 Agent 的存储
- [x] 1.3 同文件：一致性断言——`SessionsHandler.GET`（单 Agent 分支）与 `SessionDetailHandler.PUT` 对同一 Agent 传入 `get_conversation_store` 的工作区相同
- [x] 1.4 同文件：租户工作区不受影响——database 模式下 `_get_workspace_root()` 仍返回租户共享根，且会话存储解析不经过它
- [x] 1.5 移除 `tests/test_session_archive.py`、`tests/test_session_history_search.py` 中把 `_get_workspace_root` 当作会话存储解析器的 stub，并确认 RED

## 2. 实现（GREEN）

- [x] 2.1 `channel/web/web_channel.py` 新增 `_conversation_store_for(agent_id)`：按被寻址 Agent 的工作区打开会话存储，并在文档字符串中说明为何不能用工作区根
- [x] 2.2 替换 6 处按会话 ID 的解析：`SessionsHandler.GET` 单 Agent 分支、`SessionDetailHandler.PUT`、`SessionDetailHandler.DELETE`、`SessionTitleHandler.POST`、`SessionClearContextHandler.POST`、`MessageDeleteHandler.POST`
- [x] 2.3 确认 `_get_workspace_root` 与 `resolve_tenant_workspace_root` 未改动，文件面板/预览/上传/知识库调用点保持不变

## 3. 校验

- [x] 3.1 更新 `tests/test_agent_web_management.py` 中固化旧写法的源码断言
- [x] 3.2 运行 `tests/test_session_store_resolution.py`、`tests/test_session_archive.py`、`tests/test_session_history_search.py`、`tests/test_session_idor_closure.py`、`tests/test_agent_web_management.py`、`tests/test_http_policy.py` 并确认通过
- [x] 3.3 运行 `tests/test_knowledge_console_database.py`、`tests/test_workspace_edit.py`、`tests/test_tenant_read_scoping.py`，确认租户工作区语义未被改变
- [x] 3.4 用真实库复现脚本验证：修复后解析出的存储即包含该会话的库
- [x] 3.5 `openspec validate fix-session-store-agent-scope --strict` 通过

## 4. 收尾

- [x] 4.1 确认无前端/接口/schema 改动，回滚只需还原解析调用
- [x] 4.2 若实现与 spec 出现偏差，同步修订 delta spec 与 design 后再归档
