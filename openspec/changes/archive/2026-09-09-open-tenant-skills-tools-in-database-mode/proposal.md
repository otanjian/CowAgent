## Why

CowAgent 在 `identity_mode=database`（数据库多租户）模式下，技能与工具接口 `/api/skills`、`/api/skills/content`、`/api/tools` 在 `auth/http_policy.py` 中被登记为 `closed`（deferred），请求被短路为 `503 database_unavailable`，导致 Web 控制台的"技能/工具"页始终停在加载状态，租户内已同步的技能与内置工具无法查看或编辑。

## What Changes

- 将 `auth/http_policy.py` 中 `/api/tools`（GET）、`/api/skills`（GET/POST）、`/api/skills/content`（GET/POST）从 `policy: closed` 调整为 `policy: tenant`，使其在 database 模式下不再被 `enforce_http_policy` 短路成 503，而是进入 handler 做租户身份授权。
- 在 `channel/web/web_channel.py` 中，为 `ToolsHandler.GET`、`SkillsHandler.GET`、`SkillsHandler.POST`、`SkillContentHandler.GET`、`SkillContentHandler.POST` 五个入口加入 `with _db_scope() as ctx:` + `_require_read_permission(ctx, "agent.read")` 守卫：legacy 模式沿用既有 `_require_auth()`（共享控制台密码），不改变旧行为；database 模式要求当前上下文具备 `agent.read` 权限（`tenant_admin` 与内置 `member` 角色均持有）。
- 在以上 handler 的兜底 `except` 前增加 `except web.HTTPError: raise`，使授权拒绝（401/403）正确上抛，不被通用的 `Exception` 兜底误吞成 `{"status": "error"}`。
- 技能内容接口 `read_content`/`write_content` 沿用 `_skill_service()` 的租户共享根解析（经 `current_identity` 定位 `~/cow/skills`），不改变技能文件存储与读写语义。
- **BREAKING（database 模式）**：`/api/skills`、`/api/tools`、`/api/skills/content` 不再对匿名/无权限成员返回 503，而是返回 401（未登录）或 403（已登录但无 `agent.read`）；该行为仅影响 database 模式，legacy 模式访问控制不变。
- **边界放宽**：本 change 明确放宽了 `open-platform-consoles-in-database-mode` 中"`/api/tools`、`/api/skills` 保持 `closed`"的既定边界，将这两类从"平台级 deferred 消费者"改为"租户级已开放消费者"。

## Capabilities

### New Capabilities

- `tenant-skills-tools-console`: 数据库多租户模式下，租户内成员（持有 `agent.read`）对技能列表/内容与工具列表的受控读取，及对技能开关（open/close）与技能内容写回的受限修改，含鉴权边界与租户/平台隔离语义。

### Modified Capabilities

无。`openspec/specs/` 正式基线为空；本 change 使用一份 ADDED 能力补充既有产品代码的技能/工具控制台访问闭环，不重建既有配置或身份模型。

## Impact

- 需求依据：`docs/design/user-role-permission-gap-and-plan.md` 的角色/权限目录（`skill.read`、`skill.use`、`skill.manage`、`tool.execute`）与"租户共享基线"；`docs/design/menu-structure-audit-and-plan.md` 的"技能 `skills`"菜单位于智能体开发/工具与技能区。PRD-00、PRD-01～12 v1.2 原文当前缺失，记录缺口并在恢复后核对真实映射，不虚构编号。本次以现有 `agent.read` 权限作为技能/工具控制台读取门禁（两类角色均持有），不新造权限。
- 数据唯一归属：`~/cow/skills/`（及 `skills_config.json`）保有技能文件与启用状态，归属租户共享根；`identity.db` 保有账号、租户成员与角色，作为授权来源；工具为 `agent/tools/` 注册的代码，归属 Agent 能力。本次不新增技能库存储或逐租户技能复制。
- 代码范围：`auth/http_policy.py`、`channel/web/web_channel.py`（`SkillsHandler`、`SkillContentHandler`、`ToolsHandler`）与 `tests/test_http_policy.py`。保留 Python/web.py、SQLite 与现有 Web 前端。
- 跨 change 依赖：复用 `complete-enterprise-identity-access-control`（已归档）的请求授权解析（`_db_scope`/`_require_read_permission`/`_require_tenant_agent_binding`）与既有 `_skill_service` 租户共享根解析；沿用 `add-tenant-identity-access-management` 的成员角色权限目录。本 change 不实现 `skill.read` 等新权限，沿用 `agent.read` 门禁。
- 交付与验收：验证租户 `tenant_admin` 可读取技能列表/内容/工具列表、可切换技能开关，普通成员（持有 `agent.read`）可读、匿名 401、无 `agent.read` 成员 403；验证 legacy 模式访问控制保持不变。
- 兼容与恢复：保持技能文件、`skills_config.json` 结构与工具注册不变；database 模式迁移标识保持后不能切 legacy 或删除标识绕过保护。归档前协调 `open-platform-consoles-in-database-mode` 的边界说明（其 tasks 尚未勾选，需在本次说明中标注该边界已放宽，避免归档时冲突）。
