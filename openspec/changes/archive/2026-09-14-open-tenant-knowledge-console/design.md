## Context

`identity_mode=database` 下，`channel/web/route_registry.py` 把 `/api/knowledge/list|read|graph|action|import` 登记为 `policy: closed`（注释 `knowledge (deferred)`）。`auth/http_policy.py` 对 database 模式的任何 `closed` 消费者在身份解析之前直接返回 `503 database_unavailable`，因此 `KnowledgeListHandler` 等从不执行。实测：

```sh
$ curl -i "http://localhost:9899/api/knowledge/list?agent_id=knowledge-qa"
HTTP/1.1 503 Service Unavailable
{"status":"error","message":"unavailable in database identity mode","code":"database_unavailable"}
```

前端 `channel/web/static/js/console.js::loadKnowledgeView()`（约 15639 行）在拿到响应后只判断 `if (data.status !== 'success') return;`，既不清除「加载知识库中...」占位也不报错，页面永久停留在加载态。这与 `2026-09-10-tenant-owned-message-channels` 中记录的 `/api/channels` 缺陷是同一模式；同仓的 `/api/scheduler` 已用 `tasks_unavailable` 做了正确处理（`console.js` 约 15322 行）。

关键现状：

- `_is_database_identity()` 恒返回 `True`（"Database is the only identity mode."），所以 legacy 分支已无实际意义，`_guard_not_database()` 现在**总是** 503，`_db_scope()` 永远安全。
- `KnowledgeListHandler`/`KnowledgeReadHandler` 已完成数据库模式适配：`with _db_scope() as ctx` → `_require_read_permission(ctx, "knowledge.read")` → `_require_tenant_agent_binding` → `_require_private_owner` → `KnowledgeService(_get_workspace_root(agent_id=...))`。
- `KnowledgeGraphHandler` 没有 `_db_scope()`、没有权限检查、没有绑定/owner 校验，直接 `_get_workspace_root(agent_id=_request_agent_id(params))`。
- `KnowledgeActionHandler`/`KnowledgeImportHandler` 首行即 `_guard_not_database()`，随后无任何租户作用域。
- `_SIGNED_CONSOLE_PAGES["workbench.knowledge"] = {"permission": "knowledge.read", "scope": "agent"}`，成员侧边栏入口按 `knowledge.read` 与 menu grant 渲染，已具备。
- `/auth/context` 返回 `effective_permissions`、`is_tenant_admin`、`authorization_mode`，前端已有 `_baseAuthContext()` 可读取。

## Goals / Non-Goals

**Goals**

- 租户成员在控制台能真实浏览知识库目录、正文与图谱。
- 写入（新建/重命名/删除/移动/导入）在 database 模式下受控开放，且不绕过租户隔离与受保护文件规则。
- 前端不再无限加载，并按权限渲染写入口。

**Non-Goals**

- 不改 `KnowledgeService` 的文件布局、索引重建与 `index.md`/`log.md` 保护语义。
- 不改 Agent ↔ 知识库的**条目级**绑定 `knowledge_ids`（ID→条目映射仍未定义，检索/列举不据此收窄）；本次只修**数据根**：知识库页必须按所选 Agent 的 shared/own 形态解析（见决策 7）。
- 不引入资源级 grant（`role_resource_grants` 当前只覆盖 skill/tool/model/agent/menu，知识库按功能权限 + owner 判定）。
- 不动 legacy 分支（已不可达）。

## Decisions

### 1. 路由策略 `closed` → `tenant`

五条 knowledge 路由改为 `P("tenant", ...)`。选择 `tenant` 而非 `personal`/`platform`：知识库归属租户共享根，读取集合是"有效租户成员 + 功能权限"，与技能/工具控制台的归类一致。

策略项不写 `permission` 字段：门禁只在 HTTP 层完成身份域与租户成员资格校验，功能权限（`knowledge.read`/`knowledge.write`）由 handler 内的 `_require_read_permission`/写授权函数执行。这与 `/api/skills`、`/api/tools`、`/api/memory` 的现有分层一致——同一条路由的读/写需要不同权限，写死在策略表会迫使一条路由只能有一个权限。

### 2. 读：沿用既有门禁，graph 补齐

`KnowledgeGraphHandler` 与 list/read 对齐：

```python
with _db_scope() as ctx:
    _require_read_permission(ctx, "knowledge.read")
    agent_id = _require_tenant_agent_binding(ctx, _request_agent_id(params))
    _require_private_owner(ctx, agent_id)
    svc = KnowledgeService(_get_workspace_root(agent_id=agent_id))
result = svc.build_graph()
```

`build_graph()` 移到 `with` 之外：它只读文件、不再解析身份，保持在作用域内调用亦可，但移出后可复用同一份 `ctx` 语义且不延长 `use_identity` 的持有时长（与 `KnowledgeReadHandler` 的现有写法一致）。

### 3. 写：新增 `knowledge.write` + 管理员资格放行

写授权函数与 `_require_catalog_read` 同构：

```python
def _require_knowledge_write(ctx) -> None:
    if ctx is not None and (ctx.is_platform_admin or ctx.is_tenant_admin):
        return
    _require_read_permission(ctx, "knowledge.write")
```

理由：

- 目录新增权限 **不自动授予** 内置角色。`TENANT_ADMIN_DEFAULT_PERMISSIONS` 是显式九项常量，`permissions_for_roles()` 对 `tenant_admin` 只并集这九项，所以即便把 `knowledge.write` 写进 `PERMISSION_CATALOG`，tenant_admin 的**有效**权限集合仍不含它。若直接以 `knowledge.write` 门禁写入，租户管理员会被 403 挡在自家共享知识库之外。
- 与既有信任模型一致：`_require_catalog_read` 让 tenant_admin 免 grant 读本租户技能/工具目录，`_tenant_admin_owns_agent` 让 tenant_admin 免 grant 管理本租户 Agent。知识库归属租户共享根，授予 tenant_admin 写权限属于同一类"管理员管理本租户共享资源"的信任，且 `_require_tenant_agent_binding`/`_require_private_owner` 仍在前面保证不跨租户。
- 不修改"显式七项/九项"不变量，避免 `business-permission-catalog` 与 `rbac-authorization` 中关于内置默认集合的要求被推翻，也避免既有租户需要迁移其 `roles.permissions_json`。

平台管理员在 `_require_read_permission` 中已有 `authorization_mode == "all"` 短路；写路径复用同一 helper，因此平台管理员同样只需是目标租户的有效成员（由 `_db_scope` + `_require_tenant_agent_binding` 保证）。

### 4. 写 handler 去除 `_guard_not_database()`

`KnowledgeActionHandler`/`KnowledgeImportHandler` 改为：

```python
with _db_scope() as ctx:
    _require_knowledge_write(ctx)
    agent_id = _require_tenant_agent_binding(ctx, _request_agent_id(payload))
    _require_private_owner(ctx, agent_id)
    root = _get_workspace_root(agent_id=agent_id)
result = KnowledgeService(root).dispatch(action, payload)
```

`KnowledgeImportHandler` 的 `CONTENT_LENGTH` 上限检查保留在最前面（在解析身份之前就拒绝超大请求，省去无谓的身份往返）；`_raw_web_input()` 仍需在身份解析后读取。

`tests/test_knowledge_web.py` 现有三个用例 patch 了 `_guard_not_database` 并直接调用 handler（无 HTTP 上下文）。改造后这些用例需要改为 patch `_db_scope` 与写授权函数，断言契约（委派给 `dispatch`、保留 `code`/`message`）不变。

### 5. 前端：可读降级 + 权限化写入口

`loadKnowledgeView()` 的分支改为：`data.status !== 'success'` 时，识别 `database_unavailable` / `unavailable in database identity mode` → 显示 `knowledge_unavailable`；`forbidden`/403 → 显示 `knowledge_forbidden`；其余显示后端 `message` 或通用不可用文案。同时隐藏 `knowledge-panel-docs`、显示 `knowledge-empty`，并清掉 `knowledge-stats`。

写入口可见性：

```js
function canWriteKnowledge() {
    const ctx = _baseAuthContext();
    if (!ctx) return true;                       // 能力未知 -> 不猜测，服务端仍会裁决
    if (ctx.authorization_mode === 'all') return true;
    if (ctx.is_tenant_admin === true) return true;
    const perms = Array.isArray(ctx.effective_permissions) ? ctx.effective_permissions : [];
    return perms.indexOf('knowledge.write') !== -1;
}
```

`renderKnowledgeAgentSelect()` 旁挂载：`knowledge-new-menu` 按 `canWriteKnowledge()` 切换 `hidden`。`null`（能力未取到）时保持可见，避免投影未就绪时误隐藏管理入口。

### 6. 基线同步

`scripts/route-baseline.txt` 中五条 `closed` 改为 `tenant`；i18n 三语新增 key 并同步 `tests/fixtures/console_i18n_snapshot.json`。

### 7. 数据根按所选 Agent 解析

#### 问题

五个 handler 都传 `KnowledgeService(_get_workspace_root(agent_id=agent_id))`，而 `_get_workspace_root` 在 database 模式**先**返回 `resolve_tenant_workspace_root()` 就 `return`，`agent_id` 只在 legacy 回落分支才用得上：

```python
scoped_root = resolve_tenant_workspace_root(database_mode=_is_database_identity())
if scoped_root:
    return scoped_root                 # database 模式在这里就返回了
return get_agent_registry().get(agent_id).workspace   # 永远到不了
```

于是数据根恒为租户共享根，`KnowledgeService` 的 `state_dir.knowledge_dir(base=<租户根>)` 命中 `<租户根>/knowledge`（共享库）。前端智能体选择器只影响租户绑定与私有 owner 校验，**不影响数据根**——实测同一租户三个绑定 Agent（其中一个为独立知识库）返回完全相同的共享树。

而 desktop 端与 CLI 端都以 Agent 为基准：

- `desktop/.../KnowledgePage.tsx`："Which Agent's knowledge base is shown"、"Switching the viewed Agent shows a different base"；
- `cli/utils.py::get_knowledge_dir(agent_id)`：`state_dir.knowledge_dir(base=get_workspace_dir(agent_id))`；
- 运行时 `agent/prompt/builder.py` 与 `KnowledgeService(state_root_str())` 同样以 Agent 工作区为基准。

即租户分叉只改了根，丢了 per-Agent 那一跳，属于 database 模式的功能缺口。

#### 选择

新增一个只服务于知识库的根解析辅助，不改 `_get_workspace_root`（文件面板/预览/会话仍须租户根语义）：

```python
def _knowledge_workspace_root(agent_id: Optional[str]) -> str:
    if agent_id:
        try:
            from agent.registry import get_agent_registry
            workspace = get_agent_registry().get(agent_id, require_enabled=False).workspace
            if workspace:
                return str(workspace)
        except Exception:
            pass
    return _get_workspace_root(agent_id=agent_id)
```

`KnowledgeService(workspace)` 内部的 `state_dir.knowledge_dir(base=...)` 即 `_shared_or_own`：`<workspace>/knowledge` 存在（实体目录**或**指向共享库的符号链接）就用它，否则回落 `shared_root()/knowledge`。这与 CLI/运行时/desktop 完全同源，不引入第二套判定。

两个必须注意的点：

1. **回落解析必须在租户身份作用域内**：`_shared_or_own` 的回落分支读 `shared_root()`，而它按 `current_identity().tenant_id` 解析。因此五个 handler 的 `KnowledgeService(...)` 构造都必须在 `with _db_scope() as ctx:` 内完成——`KnowledgeImportHandler` 原先在作用域外构造（`root` 已解析但仍会触发回落），本次一并移入。
2. **符号链接仍视为共享**：`Path.exists()` 跟随符号链接，所以 `shared` 模式（`knowledge/` 是指向共享库的符号链接）会返回该链接路径并读出共享内容，与 `AgentAdminService._knowledge_mode_of` 的判定一致。

已知不一致（不在本次范围内，另行处理）：`_knowledge_mode_of` 对**默认 Agent** 无条件返回 `shared`（前提是"默认 Agent 拥有实例根"）；若部署里默认 Agent 已被移入私有工作区且保留实体 `knowledge/`，配置页会显示「共享」而实际读取的是它自己的目录。数据根按存在判定与运行时一致，配置页徽标属投影缺陷。

## Risks / Trade-offs

- **读开放面扩大**：任何持有 `knowledge.read` 的成员现在能读租户共享知识库。这是产品既定意图（页面文案"知识库默认全员共享，在侧栏「知识」查看和编辑"），且 `member` 默认即含 `knowledge.read`。跨租户与私有 owner 由 `_require_tenant_agent_binding`/`_require_private_owner` 拦截，并有新测试覆盖。
- **graph 之前无门禁**：若只改策略表而不补 graph 的 `_db_scope`，`tenant` 策略会放行一个不做租户校验的 handler，且 `_get_workspace_root` 在无 `use_identity` 时会落到全局默认根——这是本次必须同时修掉的部分，测试用跨租户 `agent_id` 覆盖。
- **tenant_admin 免 grant 写**：与"新增执行权限须明确授权"的边界不同，知识库写入不是执行能力，且同仓已有同类管理员信任先例；风险由"仅限本租户 + 非私有 owner + 受保护文件规则"约束。
- **纯前端降级仍然必要**：即使本次开闸，未来其他消费者被关闭时同一模式会重现，降级分支是防御性投资，也由 `.cjs` 测试固化。
- **数据根随 Agent 变化会暴露"独立库为空"的事实**：切到独立知识库 Agent 时页面可能为空（例如该 Agent 从未写过知识）。这是**正确**的读数（该 Agent 运行时也确实读不到共享库），比现在"看着共享库、实际用不到"更诚实；页面空态与「新建」入口已就绪，不会误导。
- **不回退到全局共享根**：若 Agent 在名册中查不到（绑定存在但名册缺失），辅助函数回落到 `_get_workspace_root`（database 模式即租户根），保持与本次改造前一致的保守行为，不会因为查不到而 500。

## Migration Plan

无数据迁移。代码层为策略表与 handler 授权改造，回滚只需把五条路由改回 `closed`（前端降级分支可保留）。

## Verification

- `tests/test_knowledge_console_database.py`：策略表匹配、未登录 401、无 `knowledge.read` 403、跨租户 404、私有 owner 拒绝、租户管理员写放行、`knowledge.write` 写放行、无写权限 403、`index.md`/`log.md` 保护。
- `tests/test_knowledge_web.py`：既有 handler 委托契约在改造后仍成立。
- `tests/test_http_policy.py` / `tests/test_route_registry.py` / `tests/test_consumer_closure_acceptance.py`：knowledge 不再出现在 deferred/closed 断言中；覆盖率闸门仍通过。
- `tests/test_knowledge_console_frontend.cjs`：`503 database_unavailable` 渲染 `knowledge_unavailable` 而非停在加载态；无写权限隐藏新建入口。
- `tests/test_knowledge_console_database.py`（数据根）：独立知识库 Agent 只看到自己的条目；无 `knowledge/` 的 Agent 回落到租户共享库；切换 Agent 数据范围变化；符号链接按共享处理；正文与图谱与目录同源。
- 实测：`/api/knowledge/list` 对同租户的独立库 Agent 与共享库 Agent 返回不同数据根。
- `openspec validate open-tenant-knowledge-console --strict`。
