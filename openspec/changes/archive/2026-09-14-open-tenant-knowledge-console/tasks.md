## 1. 路由策略与门禁

- [x] 1.1 `channel/web/route_registry.py`：`/api/knowledge/list`、`/api/knowledge/read`、`/api/knowledge/graph` 的 GET 与 `/api/knowledge/action`、`/api/knowledge/import` 的 POST 由 `P("closed", comment="knowledge ... (deferred)")` 改为 `P("tenant", comment=...)`，注释说明"database 模式已适配，读按 knowledge.read、写按 knowledge.write 或租户管理员资格"
- [x] 1.2 更新 `scripts/route-baseline.txt` 五条 knowledge 行由 `closed` 改为 `tenant`
- [x] 1.3 确认 `check_route_coverage` 通过（方法集合与 handler 实现一致，未被 `closed` 断言卡住）

## 2. 功能权限目录

- [x] 2.1 `auth/policy.py`：`PERMISSION_CATALOG` 追加 `"knowledge.write"`（置于 `knowledge.read` 之后或资源动作区，保持既有分组可读性）
- [x] 2.2 `auth/policy.py`：`PERMISSION_METADATA` 增加 `knowledge.write` → `{"group": "知识", "label": "编辑知识", "description": "创建/重命名/删除/移动与导入知识库内容", "scope": "tenant", "assignable": True}`
- [x] 2.3 确认 `MEMBER_DEFAULT_PERMISSIONS` / `TENANT_ADMIN_DEFAULT_PERMISSIONS` **不**包含 `knowledge.write`（不自动扩权）

## 3. 后端 handler

- [x] 3.1 新增写授权辅助 `_require_knowledge_write(ctx)`：平台管理员或本租户 `tenant_admin` 直接放行，否则 `_require_read_permission(ctx, "knowledge.write")`
- [x] 3.2 `KnowledgeGraphHandler.GET` 补齐 `with _db_scope() as ctx:` + `_require_read_permission(ctx, "knowledge.read")` + `_require_tenant_agent_binding` + `_require_private_owner`，再以解析出的 `agent_id` 构造 `KnowledgeService`
- [x] 3.3 `KnowledgeActionHandler.POST` 移除 `_guard_not_database()`，改为 `_db_scope` + `_require_knowledge_write` + `_require_tenant_agent_binding` + `_require_private_owner`，`dispatch` 使用解析后的 `agent_id` 与 workspace root
- [x] 3.4 `KnowledgeImportHandler.POST` 同上；保留 `CONTENT_LENGTH` 上限检查在身份解析之前，`_raw_web_input()` 在身份解析后读取
- [x] 3.5 保留各 handler 既有的 `except web.HTTPError: raise` / 错误 JSON 结构，确保 401/403/404 不被兜底异常吞成 `status:"error"` 普通响应

## 4. 前端

- [x] 4.1 `channel/web/static/js/console.js`：`loadKnowledgeView()` 的非成功分支清除加载占位、隐藏文档面板、清空 stats，并按 `database_unavailable`/`forbidden`/其他映射到 `knowledge_unavailable`/`knowledge_forbidden`/后端 message
- [x] 4.2 新增 `canWriteKnowledge()`（`_baseAuthContext()` 为 null 时返回 true 不猜测；`authorization_mode === 'all'` 或 `is_tenant_admin` 放行；否则查 `effective_permissions` 含 `knowledge.write`）
- [x] 4.3 `renderKnowledgeAgentSelect()` 旁按 `canWriteKnowledge()` 切换 `#knowledge-new-menu` 的 `hidden`；视图加载与 `/auth/context` 就绪后各重算一次
- [x] 4.4 `channel/web/static/js/i18n/core.js` 增加 zh / zh-TW / en 的 `knowledge_unavailable` 与 `knowledge_forbidden` 文案
- [x] 4.5 同步 `tests/fixtures/console_i18n_snapshot.json`

## 5. 测试与校验

- [x] 5.1 新增 `tests/test_knowledge_console_database.py`：策略表匹配 `tenant`、匿名 401、缺 `knowledge.read` 403、跨租户 `agent_id` 404、私有 owner 拒绝、tenant_admin 写放行、`knowledge.write` 写放行、无写权限 403、`index.md`/`log.md` 保护仍生效
- [x] 5.2 改造 `tests/test_knowledge_web.py` 既有三个 handler 契约用例（patch `_db_scope`/写授权而非 `_guard_not_database`），断言 `dispatch` 委派与错误透传不变
- [x] 5.3 核对 `tests/test_http_policy.py` / `tests/test_route_registry.py` / `tests/test_consumer_closure_acceptance.py`：三者均无把 knowledge 归入 deferred/closed 的断言（仅 `test_http_policy.py` 一处注释提及 knowledge 模式），无需改动；两个文件全绿，`test_consumer_closure_acceptance.py` 仅有与本 change 无关的既有失败（`_is_database_identity` 恒为 True）
- [x] 5.4 新增 `tests/test_knowledge_console_frontend.cjs`：503 渲染不可用文案而非加载态、无写权限隐藏新建入口、有权限保留入口
- [x] 5.5 运行相关知识库/路由/身份 pytest 与 `.cjs` 测试并确认通过：`test_knowledge_console_database.py` + `test_knowledge_web.py` + `test_http_policy.py` + `test_route_registry.py` 共 72 passed；`test_knowledge_console_frontend.cjs` 6 passed；`node --check console.js` 通过。`test_console_i18n_parity.cjs` 的 6 个新增 key 与快照一致，剩余差异来自工作区另一处未提交的 i18n 改动（`config_password*` 删除、`tasks_unavailable_desc` 改写），不在本 change 范围
- [x] 5.6 `openspec validate open-tenant-knowledge-console --strict` 通过
- [x] 5.7 浏览器实测：控制台「知识库」页显示租户共享知识库目录与正文（21 pages · 74.0 KB，含 `company`/`compliance`/`hr-benefits`/`leave`/`tax-finance`/`travel-expense` 六组），加载占位隐藏，`#knowledge-new-menu` 按 `canWriteKnowledge()` 可见；匿名/无租户仍为 400/401 而非 503

## 6. 数据根按所选 Agent 解析

- [x] 6.1 `openspec/specs/.../tenant-knowledge-console` delta 增加「知识库数据根按所选智能体解析」需求与五个场景
- [x] 6.2 新增 `_knowledge_workspace_root(agent_id)`：优先取该 Agent 工作区（`get_agent_registry().get(agent_id, require_enabled=False).workspace`），名册查不到时回落到 `_get_workspace_root`，不抛错
- [x] 6.3 `KnowledgeListHandler`/`KnowledgeReadHandler`/`KnowledgeGraphHandler`/`KnowledgeActionHandler` 的根解析改用 `_knowledge_workspace_root(agent_id)`
- [x] 6.4 `KnowledgeImportHandler` 同上，并把 `KnowledgeService(root)` 构造移入 `with _db_scope() as ctx:`（回落 `shared_root()` 必须在租户身份作用域内解析）
- [x] 6.5 新增测试：独立库 Agent 只看到自己的条目；无 `knowledge/` 的 Agent 回落租户共享库；切换 Agent 数据范围变化；符号链接按共享处理；正文与图谱与目录同源；名册缺失时回落不 500（`test_knowledge_console_database.py::KnowledgeAgentScopeTests`，先 RED（3 个用例失败于"所有 Agent 都返回共享树"）后 GREEN）
- [x] 6.6 实测 `/api/knowledge/list`：`knowledge-qa`（企业知识官，无自有 `knowledge/`）→ 租户共享 6 组；`business-analysis`（经营分析参谋，独立）→ 仅 `analysis`，且共享路径 `company/company-profile.md` 返回 `file not found`；`my-assistant-admin`（智能办公助理，独立空库）→ 空。浏览器实测知识库页切换三个智能体分别渲染 21 页 / 1 页 / 0 页，切换即时生效

## 7. 页面共享/独立口径

- [x] 7.1 delta 增加「知识库页按智能体说明共享与独立」需求与两个场景（文案中性、三语一致）
- [x] 7.2 `channel/web/static/js/i18n/core.js` 的 `knowledge_shared_hint` 三语由"默认全员共享"改为按智能体共享/独立并指向「智能体管理」
- [x] 7.3 `channel/web/chat.html` 该键的静态兜底文案同步为简体新文案
- [x] 7.4 同步 `tests/fixtures/console_i18n_snapshot.json` 三语取值
- [x] 7.5 `test_console_i18n_parity.cjs` 该键与快照一致（剩余 `config_password*` 差异仍是工作区另一处未提交改动，不在本 change 范围）

> 注：6.6 记录的"哪个智能体看到哪一份库"是当时的数据状态。之后 RSM 知识库被运维迁移进「企业知识官」的私有库（数据变更，非本 change 的代码范围），页面仍按同一规则渲染为 21 页 / 1 页 / 0 页。
