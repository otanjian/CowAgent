## Why

在 `identity_mode=database` 下，`/api/knowledge/list|read|graph|action|import` 在路由策略表中登记为 `closed`（deferred），请求被 `auth/http_policy.py` 无条件短路成 `503 database_unavailable`，从不进入 handler。控制台「知识库」页面因此在 `fetch` 成功后拿到 `status != "success"`，而 `loadKnowledgeView()` 直接 `return`、不清除占位文案，页面**永久停在「加载知识库中...」**。结果是租户已存在共享知识库内容却完全不可见、不可用，管理员在控制台看不到任何知识条目。

读取 handler（`KnowledgeListHandler`/`KnowledgeReadHandler`）已经完成数据库模式的租户作用域适配（`_db_scope` + `knowledge.read` + 租户 Agent 绑定 + 私有归属裁剪），只是被策略表挡在门外；`KnowledgeGraphHandler` 与写路径（`action`/`import`）尚未适配。这与已经收口的 `tenant-skills-tools-console`（技能/工具）和 `open-platform-consoles-in-database-mode`（配置/模型）属于同一类"已实现、未开闸"的 deferred 消费者。

## What Changes

- **BREAKING（对 `closed` 语义而言）**：把 `/api/knowledge/list`、`/api/knowledge/read`、`/api/knowledge/graph` 的 GET 与 `/api/knowledge/action`、`/api/knowledge/import` 的 POST 从 `closed` 改为 `tenant`，不再因 database 模式本身返回 503；未登录返回 401，无有效租户成员资格返回 403。
- 读路径沿用既有 `knowledge.read` 门禁与租户作用域（租户 Agent 绑定校验 + 私有归属裁剪）；`KnowledgeGraphHandler` 补齐与 list/read 一致的作用域与权限。
- 写路径（`action`/`import`）移除 `_guard_not_database()`，改为数据库模式下的受控写入：平台管理员或本租户 `tenant_admin` 资格直接放行，普通成员须持有新增功能权限 `knowledge.write`；写入同样先做租户 Agent 绑定与私有归属校验。
- 功能权限目录新增 `knowledge.write`（`group=知识`、`scope=tenant`、`assignable=true`），供自定义角色显式授予；**不**加入 `member`/`tenant_admin` 的内置默认集合（保持七项/九项不变，不因扩目录自动扩权）。
- 知识库**数据根**按所选智能体解析：以该智能体工作区为基准套用 `state_dir` 的「按存在即独立」规则（有 `knowledge/` 目录即用其自身，否则回落调用者租户共享库），不再对同租户所有智能体固定返回租户共享根；回落解析在租户身份作用域内完成。
- 前端 `loadKnowledgeView()` 对非成功响应做可读降级（识别 `database_unavailable`/关闭语义后显示明确原因），不再无限转圈；「新建」入口按 `knowledge.write` 或管理员资格渲染，避免给出必然 403 的按钮。
- 更新 `scripts/route-baseline.txt` 与 i18n 快照。

## Capabilities

### New Capabilities
- `tenant-knowledge-console`: database 多租户模式下知识库读取与写入的受控开放——接口归类为 `tenant` 域、读按 `knowledge.read` 与租户/owner 裁剪、写按 `knowledge.write` 或租户管理员资格授权、缺权与跨租户拒绝。

### Modified Capabilities
- `business-permission-catalog`: 权限目录在原九项与十三项资源动作之外增加 `knowledge.write`，并明确其不进入内置默认集合、仅供自定义角色显式分配。

## Impact

- 后端：`channel/web/route_registry.py`（knowledge 五条路由策略 `closed` → `tenant`）、`channel/web/web_channel.py`（新增 `_knowledge_workspace_root` 按智能体解析数据根并替换五个 handler 的根解析、`KnowledgeImportHandler` 的服务构造移入身份作用域；`KnowledgeGraphHandler` 补 `_db_scope`/`knowledge.read`/绑定/owner；`KnowledgeActionHandler`、`KnowledgeImportHandler` 去掉 `_guard_not_database()` 并接入 `_db_scope`、写授权、绑定与 owner；新增写授权辅助函数）、`auth/policy.py`（`PERMISSION_CATALOG` + `PERMISSION_METADATA` 增加 `knowledge.write`）。
- 前端：`channel/web/static/js/console.js`（`loadKnowledgeView` 错误降级、写入口按权限渲染、`_kbUrl` 契约不变）、`channel/web/static/js/i18n/core.js`（zh / zh-TW / en 新增不可用与无写权限文案）、`channel/web/chat.html`（如需为新建入口加禁用态）。
- 测试：新增 `tests/test_knowledge_console_database.py`（含数据根按智能体解析的新用例）；扩展 `tests/test_knowledge_web.py`；更新 `tests/test_http_policy.py` / `tests/test_route_registry.py` 的既有 `closed` 断言；新增前端 `.cjs` 断言；更新 `tests/fixtures/console_i18n_snapshot.json` 与 `scripts/route-baseline.txt`。
- 数据：无 schema 迁移；既有租户无需回填。
- 不改动：知识库文件布局与 `KnowledgeService` 语义、`index.md`/`log.md` 保护、Agent 条目级绑定 `knowledge_ids`（ID→条目映射仍未定义，检索/列举不据此收窄）、legacy 语义（`_is_database_identity()` 恒为真，database 是唯一身份模式）。
