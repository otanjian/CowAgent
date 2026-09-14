## 1. 回归测试（RED）

- [x] 1.1 新增 `tests/test_console_workspace_transport.py`：无租户选择的 `GET /api/workspace/tree` 返回 400 `missing_tenant`（门禁先校验租户选择）；携带租户但无凭据返回 401（当前均 503 `database_unavailable`）
- [x] 1.2 新增：本租户成员 `GET /api/workspace/tree` 返回 200 且 `root` 为本租户共享根；`GET /api/workspace/read?path=<本租户文件相对路径>` 返回 200 与正文
- [x] 1.3 新增：本租户成员 `GET /api/workspace/resolve?path=<他租户文件绝对路径>` 返回 403/404，响应不含 `file`/`preview_url`
- [x] 1.4 新增：`GET /api/workspace/read?path=<../ 越界路径>` 路由已开（非 503）且不返回文件内容
- [x] 1.5 新增：`POST /api/workspace/write` 跨来源 cookie 请求返回 403 `csrf_failed` 且文件字节未变；同来源请求返回 200
- [x] 1.6 新增：`POST /api/workspace/write` 指向他租户绝对路径返回 403/404 且文件未变
- [x] 1.7 确认 1.1–1.6 在实现前失败（实测 9 failed，全部因 `503 database_unavailable`）

## 2. 读路径开闸（GREEN）

- [x] 2.1 `route_registry.py`：`/api/workspace/{tree,search,resolve,meta,read}` 的 GET 由 `closed` 改为 `tenant`（保留注释说明作用域）
- [x] 2.2 `WorkspaceTreeHandler` / `WorkspaceSearchHandler` / `WorkspaceMetaHandler`：进入 `_db_scope()`；`agent` 经 `_require_tenant_agent_binding` + `_require_private_owner`，`session` 经 `_require_owned_session`；兜底 `except` 前加 `except web.HTTPError: raise`
- [x] 2.3 `WorkspaceMetaHandler` 接收并校验 `session`/`agent` 参数（与 tree 一致），避免返回非本租户根信息
- [x] 2.4 `WorkspaceReadHandler`：接入 `_db_scope()` 与 2.2 的绑定/属主校验，`_editable_target` 传入 `ctx`
- [x] 2.5 `WorkspaceResolveHandler`：接入 `_db_scope()` 与绑定/属主校验；绝对路径分支改用 `_authorize_db_file_path(ctx, abs_path)`（`forbidden` → 403，其余 → 404），不再使用 `_is_path_allowed()`
- [x] 2.6 `_editable_target(raw_path, session_id, agent_id, ctx)`：state_root 回落解析到调用者租户内（`_require_tenant_agent_binding(ctx, None)` 的结果或 `svc.root`），移除全局默认 Agent 回落；绝对路径先经 `_authorize_db_file_path` 校验
- [x] 2.7 验证：1.1–1.5 转绿

## 3. 写路径开闸与来源校验

- [x] 3.1 `route_registry.py`：`/api/workspace/write` POST 由 `closed` 改为 `tenant`
- [x] 3.2 `WorkspaceWriteHandler.POST`：`_db_scope()` 内先 `require_management_write()`，再做绑定/属主/会话校验与 `_editable_target(ctx=...)`；保留 `expected_mtime` / `code: conflict` 语义不变
- [x] 3.3 验证：1.6、1.7 转绿；同来源保存与冲突分支回归通过（`tests/test_workspace_edit.py`）

## 4. 前端可读降级

- [x] 4.1 `workspace.js`：`loadWorkspaceDir` / `runWorkspaceSearch` / 预览失败分支识别 401/403/`database_unavailable`，显示本地化文案，其余保留后端 message（新增 `wsErrorMessage`，`wsApi` 携带 `status`/`code`）
- [x] 4.2 `i18n/core.js`：zh / zh-TW / en 新增 `ws_unavailable` / `ws_forbidden` 文案；同步 `tests/fixtures/console_i18n_snapshot.json`
- [x] 4.3 前端 `.cjs` 测试断言 503/403 不再显示原始 `database_unavailable`（新增 `tests/test_console_workspace_frontend.cjs`）

## 5. 基线与回归

- [x] 5.1 `scripts/route-baseline.txt`：六条 `closed` 改为 `tenant`
- [x] 5.2 更新 `tests/test_http_policy.py`：移除/调整对 workspace 仍 `closed` 的断言，补「缺租户 400、匿名 401」顺序断言
- [x] 5.3 运行 `tests/test_console_workspace_transport.py`、`tests/test_http_policy.py`、`tests/test_route_registry.py`、`tests/test_consumer_closure_acceptance.py`、`tests/test_workspace_edit.py`、`tests/test_platform_file_browsing.py`、`tests/test_tenant_read_scoping.py` 全通过（113 passed；`test_consumer_closure_acceptance.py::test_helper_defaults_to_legacy_when_key_missing` 为改动前既有失败，与本 change 无关）
- [x] 5.4 重启服务端实测：匿名 `GET /api/workspace/tree` → 400 `missing_tenant`；携带租户无凭据 → 401；不再返回 503 `database_unavailable`。控制台右侧面板文件树/预览的可见性需登录用户刷新页面确认
- [x] 5.5 `openspec validate open-tenant-workspace-console --strict` 通过
