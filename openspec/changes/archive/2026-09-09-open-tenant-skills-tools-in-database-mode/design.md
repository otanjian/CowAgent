## Context

参 proposal.md - Why：database 多租户模式下，`/api/skills`、`/api/skills/content`、`/api/tools` 被 `auth/http_policy.py` 登记为 `closed`（deferred），请求被短路成 `503 database_unavailable`，使得 Web 控制台技能/工具页无法使用。本 change 在既有身份授权体系（`_db_scope`/`_require_read_permission`）之上，将这三类接口从"租户 deferred"改为"租户已开放"，接入 `agent.read` 门禁。

当前约束：`skills` 与 `tools` 底层分别读租户共享根 `~/cow/skills/`（含 `skills_config.json`）与 `agent/tools/` 的注册代码。`_skill_service()` 已通过 `current_identity` 解析租户共享根；`_db_scope()` 提供租户上下文解析与权限校验。`tenant_admin` 与内置 `member` 角色均持有 `agent.read`。

## Goals / Non-Goals

**Goals:**
- 让 database 模式下技能/工具接口进入 handler 做租户身份授权，而不是被短路成 503。
- 让持 `agent.read` 的租户成员能读取技能列表/技能内容、工具列表，并受限地切换技能开关、写回技能内容。
- 让无 `agent.read` 的成员被正确拒绝（401/403），且授权拒绝从兜底 `except` 中上抛。
- 保持 legacy 模式行为不变（继续用共享控制台密码鉴权）。

**Non-Goals:**
- 不实现 `skill.read`/`skill.use`/`skill.manage`/`tool.execute` 等新权限目录（沿用现有 `agent.read` 门禁）。
- 不新增技能库存储或逐租户技能复制，不改变 `~/cow/skills/` 与 `skills_config.json` 结构。
- 不开放其它仍为 `closed` 的消费者（`/upload`、`/api/workspace/*`、`/api/channels`、`/api/knowledge/*`、`/api/scheduler/*` 等保持关闭）。
- 不实现逐租户工具隔离（工具为平台侧注册的 Agent 能力，列表对持权成员只读呈现）。

## Decisions

### 决策 1：用 `_db_scope()` + `_require_read_permission(ctx, "agent.read")` 作为门禁

在 `ToolsHandler.GET`、`SkillsHandler.GET`、`SkillsHandler.POST`、`SkillContentHandler.GET`、`SkillContentHandler.POST` 五个入口的 `try` 内包一层 `with _db_scope() as ctx:`，并在其内调用 `_require_read_permission(ctx, "agent.read")`。

- `_db_scope()` 负责解析当前请求上下文（数据库模式读取会话/租户，legacy 模式降级为共享密码），非数据库模式不改变旧行为。
- `_require_read_permission(ctx, "agent.read")` 校验当前租户上下文是否持有 `agent.read`；无权限时抛 HTTP 错误。
- **备选**：新增专门的 `skill.read`/`tool.read` 权限。因 `open-platform-consoles-in-database-mode` 类工具/技能权限目录尚未实际落地、且 `member` 与 `tenant_admin` 都已持有 `agent.read`，本次沿用 `agent.read`，避免新造权限却又无法让默认成员访问。

### 决策 2：把技能开关（open/close）与技能写回同样用 `agent.read` 门禁

`SkillsHandler.POST`（切换开关）与 `SkillContentHandler.POST`（写回内容）虽然属写操作，但当前无 `skill.manage` 权限落地，故沿用 `agent.read`。`agent.read` 的成员即可读可写，`tenant_admin` 亦在内；若要收紧为仅管理员可写，需先落地 `skill.manage` 权限，本次不在范围。技能写回仍走 `_skill_service().write_content(..., expected_mtime)` 的并发冲突检测，冲突时返回 `code:"conflict"`。

### 决策 3：`except web.HTTPError: raise` 让授权拒绝上抛

原 handler 的兜底是 `except Exception`，会把权限拒绝（HTTP 错误）吞成 `{"status":"error"}`，导致前端无法区分未登录/无权限。在各 `except` 之前增加 `except web.HTTPError: raise`，使 `_require_read_permission` 抛出的 401/403 正确传播。

### 决策 4：路由策略从 `closed` 调整为 `tenant`

在 `auth/http_policy.py` 中把 `/api/tools`（GET）、`/api/skills`（GET/POST）、`/api/skills/content`（GET/POST）从 `policy: closed` 改为 `policy: tenant`。`tenant` 策略在 database 模式下允许到达 handler 做租户身份授权，而不是短路成 503；同时前端全局 fetch 包装已对同源 `/api/*` 注入 `X-Tenant-ID`，handlers 能正确解析租户上下文。

## Risks / Trade-offs

- **[租户级写操作任意成员可切技能/写内容]** → 本期沿用 `agent.read`，未做管理员才能写的细分；后续如需收紧，依赖 `skill.manage` 权限落地。已在 Non-Goals 明确，且 spec 的写回与切换 requirement 仅约束"持 `agent.read` 可执行"，未断言仅管理员。
- **[放宽 `open-platform-consoles-in-database-mode` 边界]** → 该 change 明确声明 `/api/tools`、`/api/skills` 保持 `closed`。本 change 放宽该边界，需在归档前协调该 change 的 design 说明；已在 proposal.md 的 Impact 中标注，并列入任务。
- **[技能内容读回含原始文本可能泄漏敏感信息]** → 技能库页本就要展示可编辑文本，读取限定持权成员；不做简繁改写（`read_content`）以保留可写回一致性。属既有行为，非本次引入。
- **[`.DS_Store`/`__pycache__` 等污染技能目录]** → 同步技能时已清理；读取接口只列出技能目录，不渲染隐藏文件。属既有列目录逻辑。

## Migration Plan

1. 修改 `auth/http_policy.py` 路由策略（`closed` → `tenant`，含 POST 补齐）。
2. 修改 `channel/web/web_channel.py` 五个 handler（加入 `_db_scope` + `_require_read_permission`，增加 `except web.HTTPError: raise`）。
3. 通过 `./run.sh restart` 重启 `app.py`，使新策略与守卫生效。
4. 回归验证：未登录返回 401；持 `agent.read` 的租户成员读取技能/工具/内容成功、可切换开关、可写回；无 `agent.read` 成员 403；legacy 模式共享密码访问不变。
5. 回滚：还原 `http_policy.py` 与 `web_channel.py` 到 `closed`/原 handler 即可（无数据迁移），重启生效。

## Open Questions

- 是否需要在后续 change 中落地 `skill.read`/`skill.manage`/`tool.execute` 权限目录，以把"读"和"写/管理"进一步分离？—— 不影响本 change 规格与实现（已明确沿用 `agent.read`），可推迟到权限目录统一落地时处理。
- `open-platform-consoles-in-database-mode` 的边界说明应如何重写，使其与"技能/工具已开放"保持一致？—— 归属该 change 的归档工作，本 change 仅记录放宽事实。
