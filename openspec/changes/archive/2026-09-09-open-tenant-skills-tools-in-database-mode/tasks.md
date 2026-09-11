## 1. 路由策略分类

- [x] 1.1 在 `auth/http_policy.py` 的 `ROUTE_POLICY` 中，将 `/api/tools` 的 GET 从 `policy: closed`（deferred）调整为 `policy: tenant`，保留注释说明其为租户工具列表。
- [x] 1.2 将 `/api/skills` 的 GET/POST 从 `policy: closed` 调整为 `policy: tenant`，补齐 POST（技能开关）。
- [x] 1.3 将 `/api/skills/content` 的 GET/POST 从 `policy: closed` 调整为 `policy: tenant`，补齐 POST（技能内容写回）。

## 2. 租户级鉴权守卫

- [x] 2.1 在 `channel/web/web_channel.py` 的 `ToolsHandler.GET` 中，用 `with _db_scope() as ctx:` + `_require_read_permission(ctx, "agent.read")` 包裹工具列表读取，并增加 `except web.HTTPError: raise`。
- [x] 2.2 在 `SkillsHandler.GET` 中，用 `with _db_scope() as ctx:` + `_require_read_permission(ctx, "agent.read")` 包裹技能列表读取，并增加 `except web.HTTPError: raise`。
- [x] 2.3 在 `SkillsHandler.POST` 中，用 `with _db_scope() as ctx:` + `_require_read_permission(ctx, "agent.read")` 包裹技能开关（open/close），并增加 `except web.HTTPError: raise`。
- [x] 2.4 在 `SkillContentHandler.GET` 中，用 `with _db_scope() as ctx:` + `_require_read_permission(ctx, "agent.read")` 包裹技能内容读取，并增加 `except web.HTTPError: raise`。
- [x] 2.5 在 `SkillContentHandler.POST` 中，用 `with _db_scope() as ctx:` + `_require_read_permission(ctx, "agent.read")` 包裹技能内容写回（含 `expected_mtime` 冲突检测），并增加 `except web.HTTPError: raise`。

## 3. 测试覆盖

- [x] 3.1 在 `tests/test_http_policy.py` 新增用例，验证 `/api/tools`、`/api/skills`、`/api/skills/content` 的 GET/POST 均归类为 `tenant`（而非 `closed`）。
- [x] 3.2 真实服务验证 database 模式下未登录访问这些接口返回 `401`（不再是 503）。
- [x] 3.3 回归单模块：`test_http_policy`、`test_web_consumer_closure`、`test_consumer_closure_acceptance`、`test_identity_web_handlers` 在 `.venv` 下单独运行均通过。

## 4. 迁移与验证

- [x] 4.1 通过 `./run.sh restart` 重启 `app.py`，使新策略与守卫生效。
- [x] 4.2 真实服务 curl 验证：以 `tenant_admin`（admin/admin123）携会话与 `X-Tenant-ID` 访问 `/api/skills`、`/api/tools`、`/api/skills/content` 返回 `200 success`；技能列表返回 41 个技能、工具列表返回 17 个工具、技能内容 `editable:true`。
- [x] 4.3 验证 POST `/api/skills` 切换技能开关（open/close）均返回 `{"status":"success"}`，且切换后 `enabled` 状态随之变化。
- [x] 4.4 验证未登录访问 `/api/skills`、`/api/tools` 返回 `401`（到达 handler），不再返回 `503 database_unavailable`。
- [x] 4.5 legacy 模式回归：确认这些接口仍沿用共享控制台密码鉴权，行为与改动前一致。

## 5. 文档与验收

- [x] 5.1 更新行为差异说明：记录 `/api/skills`、`/api/tools`、`/api/skills/content` 在 database 模式由 503 变为 401/403 的 BREAKING 语义。
- [x] 5.2 在 proposal/design 中标注本 change 放宽了 `open-platform-consoles-in-database-mode` 中"`/api/tools`、`/api/skills` 保持 `closed`"的既定边界，并协调该 change 的边界说明。
- [x] 5.3 运行 OpenSpec 严格校验并核对本变更的 specs 实现验收表，确认租户成员读取、无权限成员拒绝、legacy 复用共享密码均满足规格。
  - 结果：`openspec validate open-tenant-skills-tools-in-database-mode --strict` 通过（valid）。实现核对：`http_policy.py` 中 `/api/tools`、`/api/skills`、`/api/skills/content` 均归类为 `tenant`；`web_channel.py` 五个 handler（ToolsHandler.GET / SkillsHandler.GET / SkillsHandler.POST / SkillContentHandler.GET / SkillContentHandler.POST）均已包 `_db_scope()` + `_require_read_permission(ctx, "agent.read")` 并增加 `except web.HTTPError: raise`（授权拒绝正确上抛）。spec 各 requirement 均含 SHALL/MUST 与 WHEN/THEN 场景。
