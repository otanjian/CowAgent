## Context

database 是唯一身份模式（`channel/web/tenant_workspace.py::is_database_identity()` 恒为 `True`，`auth/http_policy.py::_is_database_mode()` 同理）。工作区面板的六条接口全部是 `closed`：

```154:159:channel/web/route_registry.py
    RouteEntry("/api/workspace/tree", "WorkspaceTreeHandler", "upstream", {"GET": P("closed", comment="workspace tree (deferred)")}),
    RouteEntry("/api/workspace/search", "WorkspaceSearchHandler", "upstream", {"GET": P("closed", comment="workspace search (deferred)")}),
    RouteEntry("/api/workspace/resolve", "WorkspaceResolveHandler", "upstream", {"GET": P("closed", comment="workspace resolve (deferred)")}),
    RouteEntry("/api/workspace/meta", "WorkspaceMetaHandler", "upstream", {"GET": P("closed", comment="workspace meta (deferred)")}),
    RouteEntry("/api/workspace/read", "WorkspaceReadHandler", "upstream", {"GET": P("closed", comment="workspace read (deferred)")}),
    RouteEntry("/api/workspace/write", "WorkspaceWriteHandler", "upstream", {"POST": P("closed", comment="workspace write (deferred)")}),
```

门禁在解析身份之前整体拒绝 `closed`：

```223:229:auth/http_policy.py
    policy = entry.get("policy", "closed")
    if policy == "closed" and _is_database_mode():
        # A deferred/not-yet-adapted consumer is closed in database mode and
        # must never reach a downstream handler, regardless of admin status.
        return _json_error("unavailable in database identity mode", 503,
                           "database_unavailable")
```

现状代码点（开闸后 handler 会真正执行，因此这些点决定安全性）：

- 根解析：`_get_workspace_root(session_id, agent_id)`（`web_channel.py:1097`）先看会话绑定的项目目录，再走 `resolve_tenant_workspace_root()`——database 模式返回调用者租户共享根，缺租户时抛 403 拒绝而不是回落全局默认工作区。因此 `WorkspaceService.root` 天然落在租户内。
- 相对路径：`WorkspaceService.resolve()`（`agent/workspace/service.py:85`）用 `os.path.commonpath` 拒绝越出 root 的 `..`/绝对路径；`to_workspace_rel()` 同样拒绝 root 之外的绝对路径。相对路径读写在开闸后即被限制在租户共享根或调用者私有项目目录内。
- Agent/属主校验辅助已存在：`_require_tenant_agent_binding`（`web_channel.py:1039`）、`_require_private_owner`（`1069`）、`_require_owned_session`（`983`）、`_db_scope`（`269`）。`/api/projects` 系列（已是 `tenant`）展示了同一套写法。
- 租户作用域文件根已存在：`_db_file_serve_roots(ctx)`（`web_channel.py:3404`）返回平台根（仅平台管理员）+ 本租户共享根 + 本租户全部绑定 Agent workspace；`_authorize_db_file_path(ctx, real_path)`（`3428`）据此给出 `platform`/`tenant`/`forbidden`/`not_found`。
- 全租户静态根：`_tenant_workspace_roots()`（`1230`）与 `_is_path_allowed()`（`1265`）**跨全部租户**，是为 `/preview` 公开能力令牌的纵深校验设计的——公开预览没有请求身份，只能静态列出所有可信工作区。
- 全局默认 Agent 回落：`_system_workspace_service()`（`web_channel.py:10066`）`= WorkspaceService(state_root_str())`，而 `state_root()` 在 `agent_id` 为空时返回默认 Agent 工作区（`common/state_dir.py:46`）。`_editable_target()`（`10186`）在路径不在会话工作区时回落到它。

前端调用点：`workspace.js::loadWorkspaceDir` → `/api/workspace/tree`；`openWorkspaceLink`/artifact 卡片 → `/api/workspace/resolve`；编辑器 → `/api/workspace/read`、`/api/workspace/write`。patched `window.fetch` 已为所有 `/api/*` 注入 `X-Tenant-ID`（`console.js:4270`），所以门禁的显式租户选择可得，无需 `tenant_from_resource` 标记。

## Goals / Non-Goals

**Goals**

- 已登录且获权的租户成员在控制台能真实浏览、预览并保存本租户工作区文件；面板不再因 database 模式整体 503。
- 开闸不扩大可见范围：跨租户绝对路径、平台根、操作员主目录与未知路径按不可见拒绝；落点仍在调用者租户内。
- 写操作受统一来源/CSRF 校验。

**Non-Goals**

- 不改 `WorkspaceService` 的文件布局、路径解析与冲突检测语义。
- 不改 `/preview` 能力令牌与 `/api/file`（`fix-console-artifact-download` 已收口）。
- 不开 `/api/projects/browse`（任意主机目录浏览；需另一套平台根收敛策略）。
- 不新增业务功能权限（读取沿用 `/api/projects` 的「租户成员即已授权」模型；授权由租户作用域 + Agent 绑定/属主 + 路径根共同决定）。

## Decisions

### 1. 策略 `closed` → `tenant`，不引入 `permission`

六条路由改为 `P("tenant", ...)`，与 `/api/projects` 系列一致。门禁负责「有效凭据 + 已选租户 + 该租户有效成员」（401/400/403），对象级校验（Agent 绑定、私有属主、路径根、会话属主）留在 handler。

不为这些路由写 route-level `permission`：同一条路由的读与写可能有不同授权（写还需要 CSRF 与根边界），且文件面板是 chat 消费者面的一部分，与 `/api/projects` 同类。写路径的额外约束在 handler 内执行（决策 5）。

### 2. 读 handler：`_db_scope` + 绑定/属主 + 既有根解析

```python
class WorkspaceTreeHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            try:
                params = web.input(path='', show_hidden='', session='', agent='')
                agent_id = _require_tenant_agent_binding(ctx, params.agent or None)
                _require_private_owner(ctx, agent_id)
                if params.session:
                    _require_owned_session(ctx, params.session, agent_id)
                svc = _workspace_service(params.session or None, agent_id)
                ...
            except web.HTTPError:
                raise
```

`tree`/`search`/`meta` 共用这套 `agent`/`session` 校验；`WorkspaceMetaHandler` 当前不接受 `session`/`agent` 参数，改为与 tree 一致地接收并校验，避免 meta 返回非本租户根的信息。

**注意 `web.HTTPError` 必须重新抛出**：现有 handler 的 `except Exception as e` 会把 403/404 吞成 `{"status":"error"}` 200 响应，令门禁拒绝形同虚设。所有新接入的 handler 都要在兜底 except 之前加 `except web.HTTPError: raise`（`/api/projects` 已是此写法）。

参数落地：`_workspace_service(session_id, agent_id)` 内部走 `_get_workspace_root`，database 模式已返回租户共享根；`agent_id` 用绑定校验后的值，不再用原始请求参数。

### 3. `resolve` 绝对路径：改用 `_authorize_db_file_path`

`WorkspaceResolveHandler` 的绝对路径分支当前是：

```python
abs_path = os.path.realpath(os.path.expanduser(raw_path))
if not _is_path_allowed(abs_path):
    return json.dumps({"status": "error", "message": "Path not allowed"})
```

`_is_path_allowed` 覆盖全部租户（决策 Context 已说明），开闸后即成为跨租户读取面。改为：

```python
with _db_scope() as ctx:
    if os.path.isabs(expanded):
        abs_path = os.path.realpath(expanded)
        allowed, via = _authorize_db_file_path(ctx, abs_path)
        if not allowed:
            if via == "forbidden":
                raise web.forbidden()
            raise web.notfound()
```

`_authorize_db_file_path` 的根是 `_db_file_serve_roots(ctx)`（仅本租户 + 平台根且仅平台管理员），正好是要的语义。相对路径分支继续走 `svc.stat_file` / `svc.resolve`（租户根内）。`/preview` 的公开能力令牌路径不变，仍用静态全租户根校验——它没有请求身份可用。

### 4. `_editable_target` 的 state_root 回落租户化

现在：

```python
def _editable_target(raw_path, session_id=None, agent_id=None):
    svc = _workspace_service(session_id, agent_id)
    system = _system_workspace_service()      # state_root_str() -> 无 agent 时全局默认 Agent
    try:
        rel = svc.to_workspace_rel(raw_path)
    except ValueError:
        return system, system.to_workspace_rel(raw_path)
    if svc.root != system.root and _is_system_asset_rel(rel) and not os.path.isfile(svc.resolve(rel)):
        return system, rel
    return svc, rel
```

改为在 `ctx` 存在时把 `system` 解析到调用者租户内：优先用该租户绑定的默认 Agent 工作区（`_require_tenant_agent_binding(ctx, None)` 的解析结果），否则退回 `svc.root`（租户共享根）。即：

```python
def _editable_target(raw_path, session_id=None, agent_id=None, ctx=None):
    svc = _workspace_service(session_id, agent_id)
    system = _system_workspace_service_for(ctx, svc)   # 租户内；不再回落全局默认 Agent
    ...
```

同时在校验前对绝对路径做 `_authorize_db_file_path(ctx, realpath)`，保证即使回落逻辑判断有误也不会越出租户。`_editable_target` 的两个调用点（`WorkspaceReadHandler`、`WorkspaceWriteHandler`）改为传入 `ctx`。

系统资产（`MEMORY.md` / `knowledge/` 等）在同一租户内仍可编辑，这是产品既有行为；变化只是「默认 Agent」从全局改为调用者租户的绑定默认 Agent。

### 5. 写路径补 `require_management_write()`

`WorkspaceWriteHandler.POST` 在 `_db_scope()` 之后、任何读取/写入之前调用 `channel/web/auth_handlers.py::require_management_write()`：

- cookie 会话写请求须提供与 Host 同源的 `Origin`/`Referer`（浏览器 `fetch` 默认带 Referer，同源 POST 也带 Origin，正常控制台不受影响）；
- bearer 客户端（真实认证）豁免，与 `admin_handlers.py`、`scenes/api*.py` 一致；
- 失败返回 403 `csrf_failed`，不产生写入。

这是 `console-route-lifecycle`「所有改变状态的管理写请求统一来源校验」在这一路由上的补齐；`/api/projects/select` 等既有写未接入属存量偏差，不在本次范围。

### 6. 幂等与冲突语义不变

`WorkspaceWriteHandler` 的 `expected_mtime` / `code: conflict` 逻辑、`WorkspaceService.write_text` 的原子写与边界校验保持原样。开闸只增加前置门禁，不改写语义。

### 7. 前端只做可读降级

`wsApi` 已把非成功响应抛成 `Error(data.message)`；`loadWorkspaceDir` / `runWorkspaceSearch` / 预览失败分支渲染 `e.message`。本次为 401/403/503（`database_unavailable`）增加识别，显示本地化文案（`ws_unavailable` / `ws_forbidden`），其余保留后端 message。不改 payload 契约与请求参数，因为 `window.fetch` 包装已注入 `X-Tenant-ID`。

## Risks / Trade-offs

- **读开放面扩大**：任何本租户成员（`/api/projects` 同级的成员资格）现在能读本租户共享根与全部绑定 Agent 工作区。这是 `platform-file-browsing` 已规定的读取集合（「本租户共享根与获授权 Agent workspace」），且门禁 + `_db_scope` 保证不跨租户。私有属主 Agent 由 `_require_private_owner` 拦截。
- **成员可写租户共享根**：`/api/workspace/write` 的根是租户共享根（或调用者私有项目目录），因此普通成员可改本租户共享工作区内的文件。租户是隔离边界，共享根系租户成员协作目录；跨租户在物理上被 `WorkspaceService.resolve` + `_authorize_db_file_path` 挡死。若产品后续要求「共享根写仅管理员」，需新增权限/资格判定，属独立 change（已记入 Non-Goals）。
- **`_authorize_db_file_path` 对平台根记审计**：平台管理员经 `resolve` 读平台根会写 `platform.file.read` 审计，与 `/api/file` 一致，属预期。
- **`except web.HTTPError: raise` 遗漏会静默失效**：现有 handler 的兜底 except 是 200 + `status:error`，必须逐 handler 加重新抛出，否则 403 变成「面板显示 error」而非 HTTP 拒绝。测试须断言 HTTP 状态码而非仅 JSON。
- **回落租户化可能暴露「独立库/独立工作区为空」**：切到某 Agent 时读到的是它自己的目录，可能为空。这是正确读数，面板空态已就绪。
- **不引入 route-level permission**：若未来需要「仅持某权限可浏览工作区」，须另立 capability 与默认授权决策，避免把只读能力与 chat 消费者面永久绑定。

## Migration Plan

无数据迁移。代码层为策略表与 handler 授权改造；回滚只需把六条路由改回 `closed`（前端降级分支可保留）。前端文案变更无回滚依赖。

## Verification

- `tests/test_console_workspace_transport.py`（新增）：
  - 匿名 `GET /api/workspace/tree` → 401（不是 503）；
  - 有凭据无租户选择 → 400 `missing_tenant`；
  - 本租户成员 `GET /api/workspace/tree` / `read` → 200，根为租户共享根；
  - 本租户成员 `GET /api/workspace/resolve?path=<他租户文件绝对路径>` → 403/404，响应不含元数据与 `preview_url`；
  - `GET /api/workspace/read?path=<越出工作区的路径>` → error 且不返回内容；
  - `POST /api/workspace/write` 跨来源 cookie 请求 → 403 `csrf_failed` 且无写入；同来源 → 200；
  - `POST /api/workspace/write` 无写权限场景（越界路径）→ 拒绝且文件未变。
- `tests/test_http_policy.py`：六条路由不再是 `closed`；缺租户 400、无凭据 401 的顺序断言。
- `tests/test_route_registry.py` / `scripts/route-baseline.txt`：六条策略同步为 `tenant`，覆盖率闸门通过。
- `tests/test_workspace_edit.py`：既有编辑契约在改造后仍成立。
- `tests/test_console_workspace_frontend.cjs`（或并入既有前端测试）：503/403 渲染本地化不可用/无权限文案，不显示原始 `database_unavailable`。
- 实测：`curl /api/workspace/tree` 匿名 401、带凭据 200；`/api/workspace/resolve` 跨租户绝对路径被拒。
- `openspec validate open-tenant-workspace-console --strict`。
