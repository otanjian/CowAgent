## Why

database 身份模式下，控制台对话页右侧的工作区面板（`#workspace-panel`：「文件」tab 的文件树/搜索，以及「预览」tab 的正文与编辑）整体不可用，面板只显示一句后端英文 `unavailable in database identity mode`。

实测（本机 9899，服务端已运行）：

```sh
$ curl -s -i "http://localhost:9899/api/workspace/tree"
HTTP/1.1 503 Service Unavailable
{"status": "error", "message": "unavailable in database identity mode", "code": "database_unavailable"}

$ curl -s -i "http://localhost:9899/api/workspace/resolve?path=/Users/.../tmp/test-file.txt"
HTTP/1.1 503 Service Unavailable
{"status": "error", "message": "unavailable in database identity mode", "code": "database_unavailable"}
```

`/api/workspace/{tree,search,resolve,meta,read}` 与 `/api/workspace/write` 在权威清单 `channel/web/route_registry.py` 中登记为 `closed`（注释 `workspace ... (deferred)`）。`auth/http_policy.py` 对 database 模式的任何 `closed` 消费者在身份解析之前直接返回 `503 database_unavailable`，因此六个 handler 从不执行。前端 `channel/web/static/js/workspace.js::wsApi` 在 `status != 'success'` 时抛出 `data.message`，`loadWorkspaceDir` 把该字符串原样渲染成警告占位——用户看到的就是后端文案，与具体文件无关。

这与已经收口的 `fix-console-artifact-download`（`/api/file` 下载、`/preview` 能力令牌预览）属于同一条文件链路：消息卡片的工件下载/预览已修好，但**面板自身**的文件树与预览仍被关闭，预览 tab 同理（它调用 `/api/workspace/resolve`）。

handler 侧只完成了部分适配：根解析已经走 `_get_workspace_root`（database 模式返回调用者租户共享根），`_is_path_allowed` 也已纳入租户/Agent 工作区。但有两个跨租户缺口必须在开闸同时补掉，不能只改策略值：

1. `WorkspaceResolveHandler` 的绝对路径分支用 `_is_path_allowed()` 授权，而该函数的允许根是**全部租户**共享根与全部绑定 Agent 工作区（它是为 `/preview` 能力令牌的纵深校验设计的、与请求身份无关的静态列表）。原样开闸后，A 租户成员用绝对路径即可 resolve B 租户的文件并拿到 `preview_url`。
2. `_editable_target()` 在路径不在会话工作区时回落到 `_system_workspace_service()`，即 `state_root_str()`；无 `agent_id` 时它解析到**全局默认 Agent** 的工作区。非默认租户以相对路径读 `MEMORY.md` / `knowledge/...` 时会落到默认 Agent 目录，跨租户。

## What Changes

- **路由策略**：`/api/workspace/{tree,search,resolve,meta,read}` 的 GET 与 `/api/workspace/write` 的 POST 由 `closed` 改为 `tenant`，不再因 database 模式本身返回 503；未登录 401、未给出租户选择 400、无有效租户成员资格 403。
- **handler 作用域**：六个 handler 进入 `_db_scope()`；`agent` 参数经 `_require_tenant_agent_binding` + `_require_private_owner` 校验，`session` 参数经 `_require_owned_session`（存在时）校验；根解析继续走 `_get_workspace_root`（会话私有项目目录 / 租户共享根）。
- **绝对路径租户化**：`WorkspaceResolveHandler` 的绝对路径分支改用租户作用域的 `_authorize_db_file_path(ctx, path)`，不再用覆盖全部租户的 `_is_path_allowed()`；平台根仅平台管理员可读，其余越界按不可见拒绝（404/403），不泄漏存在性。
- **系统资产回落租户化**：`_editable_target` 的 state_root 回落改为在调用者租户作用域内解析（该租户绑定的默认 Agent / 租户共享根），移除「无 agent 即全局默认 Agent」的跨租户回落；路径解析仍由 `WorkspaceService.resolve` 保证不越出所选根。
- **写路径来源校验**：`/api/workspace/write` 在写之前经统一 `require_management_write()`（Origin/Referer 与 Host 同源；bearer 真实认证豁免），并保留既有工作区边界与 `expected_mtime` 冲突检测。
- **前端可读降级**：面板拿到 401/403/503 时显示本地化的明确不可用/无权限文案（识别 `database_unavailable` / `forbidden`），不再把网关文案当业务错误直接显示；三语 i18n + 快照同步。
- 更新 `scripts/route-baseline.txt`。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `platform-file-browsing`: 文件服务作用域扩展到控制台工作区面板的路由族（tree/search/resolve/meta/read/write）——已适配的消费者必须开闸；绝对路径与系统资产回落按调用者租户作用域授权，MUST NOT 以覆盖全部租户的静态根列表作为授权依据。
- `database-runtime-consumers`: 浏览器传输契约补充控制台工作区面板的浏览/预览/保存必须对已登录且获权成员可用，不得因 database 模式返回 503，也不得静默失败。
- `console-route-lifecycle`: 管理写统一来源校验的覆盖清单纳入控制台工作区文件保存 `POST /api/workspace/write`。

## Impact

- 后端：`channel/web/route_registry.py`（六条路由策略 `closed` → `tenant`）、`channel/web/web_channel.py`（六个 handler 接入 `_db_scope` 与绑定/属主校验；`_editable_target` 增加 ctx 并租户化 state_root 回落；`WorkspaceResolveHandler` 绝对路径改用 `_authorize_db_file_path`）。
- 前端：`channel/web/static/js/workspace.js`（错误降级文案）、`channel/web/static/js/i18n/core.js`（zh / zh-TW / en 新增不可用与无权限文案）。
- 测试：新增 `tests/test_console_workspace_transport.py`（未登录 401、缺租户 400、本租户 tree/read 200、跨租户绝对路径 resolve 拒绝、路径逃逸拒绝、写来源校验）；更新 `tests/test_http_policy.py`、`tests/test_route_registry.py`、`scripts/route-baseline.txt`、`tests/fixtures/console_i18n_snapshot.json`；扩展 `tests/test_workspace_edit.py`。
- 不改动：`agent_workspace`/租户目录布局、数据库 schema、`WorkspaceService` 的解析与冲突语义、`/preview` 能力令牌与 `/api/file` 的既有行为、`/api/projects/browse`（仍 `closed`，属任意主机目录浏览的收敛范围，另行处理）。
