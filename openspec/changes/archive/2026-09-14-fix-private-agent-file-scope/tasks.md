## 1. 回归测试（RED）

- [x] 1.1 新增 `tests/test_private_agent_file_scope.py`：同租户普通成员（非 `tenant_admin`）以显式共享 `agent` 参数 + 绝对路径请求他成员私有 Agent workspace 内的文件，`GET /api/workspace/resolve` MUST 返回 403/404，响应不含 `file` / `preview_url` / `raw_url`
- [x] 1.2 新增：同一场景 `GET /api/workspace/read` MUST 拒绝且不含文件正文
- [x] 1.3 新增：同一场景 `POST /api/workspace/write` MUST 拒绝且文件字节未变
- [x] 1.4 新增：`GET /api/file?path=<他成员私有 Agent 文件绝对路径>`（不带 `agent_id`）MUST 拒绝，不返回私有字节
- [x] 1.5 新增：`GET /api/file?path=<同一路径>&agent_id=<共享 agent>` MUST 按冲突拒绝（自报标识不得成为授权来源）
- [x] 1.6 新增（相对寻址向量）：`resolve`/`read` 以相对路径（`agents/<private-agent>/…`，即私有 Agent 嵌套在租户共享根之下）请求 MUST 拒绝；`tree?path=agents` MUST NOT 出现该私有 Agent 目录名；`search` MUST NOT 返回该私有 Agent 的路径（含「绕过过滤器时确实能命中」的非空泛断言）
- [x] 1.7 新增（反向，防过度收敛）：私有 Agent 的**属主本人**与 `tenant_admin` 请求同一路径 MUST 成功（含 `tree` 仍列出该 Agent）；共享 Agent 的 workspace 文件对本租户成员 MUST 成功；租户共享根文件 MUST 成功；共享 Agent 文件的 `/api/file` MUST 成功；平台根对平台管理员 MUST 成功且仍记 `platform.file.read` 审计
- [x] 1.8 新增：归属不唯一（两个 Agent workspace 互相同构/嵌套）时 MUST 拒绝而非任选其一放行（`AgentRegistry` 拒绝同 workspace 配置，故在 `_db_file_root_owners` 注入退化根列表验证）
- [x] 1.9 确认 RED 失败：实测 9 failed（`resolve` 绝对/相对 200 且含 `preview_url`、`read` 返回正文、`write` 改写成功、`/api/file` 返回私有字节、`tree` 列出 `private-agent`）

## 2. 归属解析（GREEN）

- [x] 2.1 `channel/web/web_channel.py`：新增 `_db_file_root_owners(ctx) -> list[tuple[str, Optional[str]]]`（平台根与租户共享根 `agent_id=None`，绑定 Agent 根带 `agent_id`），并保留 `_db_file_serve_roots(ctx) -> list[str]` 的扁平契约（由前者投影，既有 patch 接缝不变）
- [x] 2.2 新增 `_db_path_owner(real_path, roots)` / `_owner_of_db_path(ctx, real_path)`：取最长匹配为归属；两个不同 Agent 等长命中 → `("ambiguous", None)`；Agent 根与共享根等长（Agent workspace 即租户共享根）→ 按共享语义放行，避免单 Agent 布局被误判为歧义；无命中 → `("none", None)`；平台根 → `("platform", None)`；共享根 → `("shared", None)`；Agent 根 → `("agent", agent_id)`
- [x] 2.3 抽出纯判定 `_db_path_owner_forbidden(ctx, agent_id) -> bool`（私有属主非空且调用者既非属主也非 `tenant_admin`），`_require_private_owner` 改为调用它并抛 403，规则单一来源
- [x] 2.4 新增 `_db_path_visible(ctx, real_path, roots=None) -> bool`（单一归属判定入口，`ambiguous` 与无权私有 Agent 返回 False；`roots` 允许列举时只解析一次）
- [x] 2.5 `_authorize_db_file_path`：平台根分支与 `platform.file.read` 审计保持不变；其后按 `_db_file_serve_roots` 判租户包含（保留 patch 接缝），命中后追加归属细化，`ambiguous` → `(False, "not_found")`，无权私有 Agent → `(False, "forbidden")`
- [x] 2.6 验证：1.1–1.5、1.8 转绿；1.7 反向用例保持通过

## 3. 调用面收敛（相对路径与列举）

- [x] 3.1 `FileServeHandler.GET`：归属始终从路径解析（授权来源）；`params.agent_id` 仅作冲突检测，与解析归属不一致时拒绝；保留既有绑定/属主/`agent.read` 校验
- [x] 3.2 `WorkspaceResolveHandler` 相对路径分支：`svc.stat_file` / 系统资产回落取到 `entry["abs_path"]` 后经 `_db_path_visible` 校验，不可见即拒绝
- [x] 3.3 `_editable_target`：相对返回分支对 `target.resolve(rel)` 经 `_db_path_visible` 校验；state_root 回落目标（`_workspace_system_service`）执行同一属主判定，不通过则退回 `svc.root`；绝对路径分支继续经 `_authorize_db_file_path`
- [x] 3.4 `WorkspaceTreeHandler` / `WorkspaceSearchHandler`：新增 `_visible_entries`，对每条条目的真实路径跑 `_db_path_visible`，不可见条目从结果中移除（目录与文件一致，避免泄漏路径存在性），且在 `_decorate_entry` 之前过滤以免为其签发 URL
- [x] 3.5 `WorkspaceResolveHandler` 绝对路径分支：确认只用 `_authorize_db_file_path`（不残留 `_is_path_allowed`），`forbidden` → 403、其余 → 404
- [x] 3.6 验证：1.4–1.6 转绿；`tests/test_console_workspace_transport.py` 既有跨租户/逃逸/CSRF 用例保持通过

## 4. 基线与回归

- [x] 4.1 更新 `tests/test_platform_file_browsing.py`：新增 `test_member_denied_another_members_private_agent_file`（成员 `forbidden`、`tenant_admin` 放行），`_ctx` 增加 `is_tenant_admin`，保留其 `_db_file_serve_roots` patch 夹具可用
- [x] 4.2 相关套件全通过：`test_private_agent_file_scope` / `test_platform_file_browsing` / `test_platform_file_and_channels_phase1` / `test_console_workspace_transport` / `test_console_file_transport` / `test_workspace_edit` / `test_tenant_read_scoping`（80 passed），外加 `test_knowledge_web` / `test_tenant_default_agent_selection` / `test_doc_edit` / `test_default_agent_tenant_shared` / `test_route_registry` / `test_http_policy` / `test_web_consumer_closure`（合计 193 passed）；全量 `pytest tests/` 与改动前基线同为 **38 failed / 同一失败清单**（均为在办的飞书/SSRF/头像/PDF 等无关用例），无新增失败
- [ ] 4.3 重启服务端实测：**未执行** —— 本机 9899 上的 `python -u app.py`（PID 93847，22:38 启动，父进程 launchd）由用户启动且早于本次改动，属用户正在使用的实例，未擅自重启。等价验证由 `tests/test_private_agent_file_scope.py` 覆盖（`build_web_app()` 真实 WSGI 应用 + 真实登录 Cookie + 真实身份库），并另跑 `build_web_app()` 冒烟确认新代码可加载。需要现场验证时请重启该实例
- [x] 4.4 `openspec validate fix-private-agent-file-scope --strict` 通过
