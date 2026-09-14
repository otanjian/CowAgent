## 1. 回归测试（RED）

- [x] 1.1 新增 `tests/test_console_file_transport.py`：`GET /api/file?path=<本租户智能体工作区文件>` 仅带 cookie（无租户头）返回 200 与文件字节（当前 400 missing_tenant）
- [x] 1.2 新增：`GET /api/file?path=<他租户文件>` 无租户头返回 403，响应不含文件内容
- [x] 1.3 新增：请求头租户与文件派生租户冲突返回 400 `conflicting_tenant`
- [x] 1.4 新增：`GET /api/file` 无凭据返回 401（资源派生不是认证绕过）
- [x] 1.5 新增：`GET /api/file?path=<不属于任何工作区的路径>` 返回 404，不泄漏内容
- [x] 1.6 新增：`GET /preview/<hmac-token>/<name>` 无 cookie 返回 200 且含文件内容（当前 404 not found）
- [x] 1.7 新增：`/preview` 对不可信目录的令牌仍返回 404
- [x] 1.8 确认 1.1、1.2、1.3、1.4、1.5、1.6 在实现前失败（实测 6 failed / 1 passed）

## 2. 实现（GREEN）

- [x] 2.1 `web_channel`：新增 `_tenant_owning_path(svc, real_path)`，由租户共享根 + 绑定智能体 workspace（服务端来源）解析文件所属租户，取最具体包含根
- [x] 2.2 `web_channel`：新增 `_file_identity_scope()`（认证 → 由文件派生租户 → 冲突检查 → 成员校验 → 发布身份）
- [x] 2.3 `FileServeHandler.GET()` 改用 `_file_identity_scope()`，保留绑定/属主/`agent.read`/`_authorize_db_file_path` 对象级校验
- [x] 2.4 `route_registry.py`：`/api/file` GET 增加 `tenant_from_resource=True`（策略仍为 `tenant`；基线不含该标记，无需改动）
- [x] 2.5 `web_channel`：新增 `_tenant_workspace_roots()`（全部租户共享根 + 全部绑定智能体 workspace）
- [x] 2.6 `_is_path_allowed()` 纳入 `_tenant_workspace_roots()`，并移除会因无身份构造 `web.HTTPError`（污染 `web.ctx.status`/响应头）的 `_get_workspace_root()` 调用

## 3. 验证

- [x] 3.1 `tests/test_console_file_transport.py` 全通过（7 passed）
- [x] 3.2 回归：`test_http_policy.py`、`test_route_registry.py`、`test_platform_file_browsing.py`、`test_web_consumer_closure.py`、`test_console_upload_transport.py`、`test_http_gate.py`、`test_platform_file_and_channels_phase1.py`（116 passed）
- [x] 3.3 回归：`test_session_idor_closure.py`、`test_chat_identity_context.py`、`test_web_sse_replay.py`、`test_history_agent_workspace.py`、`test_tenant_channel_console_scope.py`、`test_identity_scope_gate.py`、`test_tenant_read_scoping.py`、`test_workspace_edit.py`、`test_team_file.py`（113 passed）
- [x] 3.4 `openspec validate fix-console-artifact-download --strict` 通过
- [x] 3.5 真实实例根解析：目标工件 `_tenant_owning_path` 归属默认租户 `tnt_xNHlQIA2XP-z6nQG`，`_is_path_allowed` 为 True
- [x] 3.6 重启服务端到端：`GET /preview/<token>/<name>` 返回 200（17909 B，含注入的预览滚动条样式）；匿名 `GET /api/file?path=<工件>` 返回 401（不再是 400 missing_tenant）

## 4. 收尾

- [x] 4.1 路由基线无需变更：`/api/file` 策略仍为 `tenant`，仅增加资源派生标记（基线格式不含该标记）
- [x] 4.2 记录验收证据（见本文件 3.5 / 3.6 与 proposal.md）
