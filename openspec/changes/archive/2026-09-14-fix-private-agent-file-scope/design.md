## Context

### 现状接入点

文件服务在 database 模式下的授权只有一个判据——**租户包含关系**：

```python
# channel/web/web_channel.py:3406
def _db_file_serve_roots(ctx) -> list:
    """Roots a database-mode caller may serve files from."""
    roots = []
    if getattr(ctx, "is_platform_admin", False):
        roots.append(_platform_file_root())
    shared = svc.tenant_shared_root(ctx.tenant_id) if getattr(ctx, "tenant_id", None) else None
    if shared:
        roots.append(os.path.realpath(shared))
    if getattr(ctx, "tenant_id", None):
        for agent_id in svc.tenant_agent_ids(ctx.tenant_id):      # ← 全部绑定 Agent，不分共享/私有
            roots.append(os.path.realpath(registry.get(agent_id).workspace))
    return roots

# channel/web/web_channel.py:3428
def _authorize_db_file_path(ctx, real_path: str) -> tuple:
    # 平台根：非平台管理员 → (False, "forbidden")；平台管理员 → 审计后 (True, "platform")
    for root in _db_file_serve_roots(ctx):
        if root == platform_root:
            continue
        if os.path.commonpath([real_path, root]) == root:
            return True, "tenant"                              # ← 首次命中即放行，无属主判定
    return False, "not_found"
```

属主校验确实存在，但只挂在**请求声明的 agent 参数**上：

```python
# channel/web/web_channel.py:10105
def _workspace_request_scope(ctx, session_id, agent_id):
    resolved = _require_tenant_agent_binding(ctx, agent_id)   # agent 参数绑定租户
    _require_private_owner(ctx, resolved)                     # agent 参数私有属主
    ...
```

两个调用面因此不一致：

| 入口 | 校验对象 | 私有属主生效？ |
| --- | --- | --- |
| `/api/workspace/{tree,search,meta}` 相对路径 | 声明的 `agent` 参数 | ✅ 对声明的 agent |
| `/api/workspace/tree` 列举 | 声明的 `agent` 参数 | ❌ 直接列出 `agents/<private-agent>/` |
| `/api/workspace/resolve` 相对路径（含共享根下目录嵌套） | 路径落在哪个租户根 | ❌ 不校验路径所属 Agent |
| `/api/workspace/read` / `write` 相对路径 | 同上 | ❌ 同上 |
| `/api/workspace/resolve` 绝对路径 | 路径落在哪个租户根 | ❌ 不校验路径所属 Agent |
| `/api/workspace/read` / `write` 绝对路径 | 同上 + `_editable_target` 回落循环 | ⚠️ 仅被回落循环「顺带」挡住 |
| `/api/file?path=…`（无 `agent_id`） | 路径落在哪个租户根 | ❌ 完全不校验 |

三个向量都已实测复现（`tests/test_private_agent_file_scope.py` 的 RED 运行，9 项失败）：`resolve` 绝对/相对路径均返回 200 且含 `preview_url`；`read` 返回他人私有文件正文；`write` 直接改写该文件（200）；`/api/file` 在不带 `agent_id` 时返回私有字节；`tree?path=agents` 直接列出 `private-agent`。`agent` 参数声明为一个共享 Agent 即可通过前两层校验——因为它校验的是参数，不是被寻址文件。

其中相对路径/目录列举这一向量说明问题不是「绝对路径没校验」，而是**归属校验从未以被寻址路径为准**：租户共享根之下嵌套的 Agent workspace 会被共享根「吃掉」，这正是既有规范里写的「不因…同租户目录嵌套公开私有内容」。

### 行为矩阵（目标态）

| 被寻址资源 | 调用者 | 结果 |
| --- | --- | --- |
| 平台根 | 平台管理员 | 允许，记 `platform.file.read` 审计 |
| 平台根 | 非平台管理员 | 403（不变） |
| 本租户共享根 | 本租户成员 | 允许（共享语义不变） |
| 共享 Agent 的 workspace | 本租户成员 | 允许 |
| **私有 Agent 的 workspace** | **属主本人** | 允许 |
| **私有 Agent 的 workspace** | **`tenant_admin`** | 允许（管理读取） |
| **私有 Agent 的 workspace** | **其他普通成员** | **拒绝（403）** |
| 归属不唯一（多个 Agent 根同时包含） | 任何人 | 拒绝（404） |
| 其他租户 / 未知路径 | 任何人 | 拒绝（404/403，不泄漏存在性） |

## Decisions

### 1. 新增「根 → 所属 Agent」的归属解析，而不是在 `_authorize_db_file_path` 里重列根

新增：

```python
def _db_file_root_owners(ctx) -> list:
    """[(realpath, agent_id_or_None)] —— 平台根与共享根 agent_id 为 None。"""

def _db_file_serve_roots(ctx) -> list:
    """既有扁平根列表契约：返回 [root for root, _ in _db_file_root_owners(ctx)]。"""
```

`_db_file_serve_roots` 保持**返回扁平列表**不变，因为 `tests/test_platform_file_browsing.py` 直接 patch 它做夹具；把它改成 `(root, agent)` 元组会破坏该接缝，也会让其他调用点（`_db_file_serve_roots` 只被 `_authorize_db_file_path` 与测试引用）承担不必要改动。归属解析走新函数，`_authorize_db_file_path` 两个都查（保持 patch 兼容）或直接改用新函数时同步改测试。

### 2. 取**最具体**的匹配根；不唯一则失败关闭

`os.path.commonpath` 是前缀匹配：`shared_root` 自然包含其下的 `shared_root/agents/<id>`，所以「首个命中即返回」的现有顺序（平台 → 共享 → 各 Agent）会把 Agent workspace 内的文件判成 `tenant`，属主判定就永远不会触发。因此必须：

1. 收集**全部**命中的根；
2. 取路径最长（最具体）的那个作为归属；
3. 若存在两个**不同 Agent** 的最长匹配（等长且不同 agent_id），MUST 拒绝 —— 配置退化（两个 Agent 指向同一或嵌套目录）不能任选其一放行。

这同时修掉「共享根嵌套 Agent workspace」时归属被共享根吃掉的问题。

### 3. 属主判定复用既有 `_require_private_owner` 的语义，但以返回值表达

`_require_private_owner` 抛 `web.HTTPError`，而 `_authorize_db_file_path` 的契约是返回 `(allowed, via)`，`FileServeHandler` 再按 `via` 决定 403/404。为避免同一规则两套表达，抽出纯判定：

```python
def _db_path_owner_forbidden(ctx, agent_id: Optional[str]) -> bool:
    """True 当 agent 私有且调用者既非属主也非 tenant_admin。"""
```

`_require_private_owner` 改为调用它并抛错（行为不变），`_authorize_db_file_path` 用它返回 `(False, "forbidden")`。规则单一来源：私有属主非空 → `ctx.is_tenant_admin` 或 `owner == ctx.user_id` 才放行；平台管理员在租户层**不是**例外（与既有注释一致）。

### 4. `_authorize_db_file_path` 在租户判定之后追加属主判定

```python
def _authorize_db_file_path(ctx, real_path: str) -> tuple:
    # 平台根分支保持不变（含 platform.file.read 审计）
    kind, agent_id = _owner_of_db_path(ctx, os.path.realpath(real_path))
    if kind == "ambiguous":
        return False, "not_found"
    if kind == "agent" and _db_path_owner_forbidden(ctx, agent_id):
        return False, "forbidden"
    if kind == "none":
        return False, "not_found"
    return True, "tenant"
```

调用点因此**零改动**即获得属主校验：`WorkspaceResolveHandler` 绝对路径（已用 `_authorize_db_file_path`）、`_editable_target` 绝对路径（已用）、`FileServeHandler`（已用）。`read`/`write` 的「靠回落循环顺带挡住」被替换为显式判据。

### 5. `/api/file` 与自报 `agent_id` 的关系

`FileServeHandler` 现在只在 `params.agent_id` 非空时校验属主。改为：

- 归属**始终**从路径解析（授权来源）；
- 若请求同时带上 `agent_id`，只做**冲突检测**：与路径解析出的 agent 不一致时拒绝（`not_found`），避免把自报标识当作授权来源（与 `platform-file-browsing` 既有「客户端自报路径、租户头或 Agent 标识 MUST NOT 成为授权来源，只可用于冲突检测」一致）。

`_decorate_entry` / `WorkspaceResolveHandler` 生成的 `raw_url` 保持不带 `agent_id`（浏览器原生导航不需要它），因为授权已不依赖它。

### 6. `_editable_target` 的回落目标也校验归属

`_workspace_system_service(ctx, svc)` 目前用 `_resolve_tenant_default_agent(ctx)`，在「本租户无共享 Agent」时会落到私有 Agent。改为在解析后对其执行同一属主判定，不通过则退回 `svc.root`（租户共享根，已租户化）；两者都不可用时抛 404，MUST NOT 读取无权 Agent 的资产。

### 7. 单一归属判定入口 `_db_path_visible`，覆盖全部寻址方式

归属校验必须是**以被寻址路径为准**的单一判定，而不是按入口各写一份：

```python
def _owner_of_db_path(ctx, real_path) -> tuple:
    """('platform'|'shared'|'agent'|'ambiguous'|'none', agent_id_or_None)"""

def _db_path_visible(ctx, real_path) -> bool:
    """False 当归属为 ambiguous，或为无权读取的私有 Agent。"""
    kind, agent_id = _owner_of_db_path(ctx, real_path)
    if kind == "ambiguous":
        return False
    if kind == "agent" and _db_path_owner_forbidden(ctx, agent_id):
        return False
    return True
```

三个接入点：

- `_authorize_db_file_path`（`/api/file`、`resolve`/`read`/`write` 的绝对路径、`_editable_target` 绝对分支）：先按 `_db_file_serve_roots` 判租户包含（保留既有 patch 接缝），再按归属返回 `(False, "not_found")` / `(False, "forbidden")`。
- 相对路径分支（`resolve` 的 `svc.stat_file`、`_editable_target` 的相对返回）：路径已由 `WorkspaceService.resolve` 证明在调用者租户根内，只需归属细化——不可见即拒绝。
- `tree`/`search`：对每个条目的真实路径跑 `_db_path_visible`，不可见的条目**从结果中移除**（不只是拒绝直接访问），因为规范要求不泄漏路径存在性。

为什么共享根不能豁免：Agent workspace 嵌套在租户共享根之下是既有布局（`shared_root/agents/<id>`），而共享根的语义是「本租户成员协作目录」，它 MUST NOT 覆盖其内部显式标记为私有的 Agent 资产。这也是最具体匹配根（决策 2）必须存在的原因。

### 8. 不新增 route-level `permission`

沿用 `open-tenant-workspace-console` 的决策：租户是隔离边界，文件面板不引入 `workspace.read`/`agent.read` 门禁。本 change 只补齐**既有**的私有属主规则，不扩大也不新增授权维度。若产品要求「共享根写仅管理员」或按 `agent.read` 门禁浏览，属独立 change。

## Risks and Trade-offs

- **`tenant_admin` 仍可读全租户私有 Agent**：与 `_require_private_owner` 既有语义一致（管理读取），本 change 不收紧。若产品后续要求私有 Agent 对管理员也不可见，需独立 change。
- **`/preview` 令牌路径不新增属主校验**：令牌是服务端签发的能力凭证，签发前已过 resolve 的归属校验；令牌一旦签发即可读取该目录（含相对子资源），这是既有能力模型，本 change 不改变。
- **同一 `shared_root` 下的用户私有项目目录**：`_db_file_serve_roots` 放行整个共享根，成员间的项目目录路径猜测是**既有** gap（已在 `docs/superpowers/specs/2026-09-09-user-private-projects-database-mode.md` 记录），本 change 明确不处理，避免把「Agent 私有」与「用户私有项目」两套归属混在一处。
- **`tenant_agent_ids` 的绑定竞态**：归属解析在请求内读绑定表；绑定被并发重绑时以本次读取为准，与既有 `_require_private_owner` 的竞态窗口相同，不新增一致性要求。

## Migration and Recovery

- 无 schema 变更、无数据迁移、无 feature flag；纯授权判据收敛。
- 回滚即还原 `_authorize_db_file_path` 的租户判定（去掉属主判定与归属解析调用），既有共享根/共享 Agent/平台根行为不受影响。
- 预期只影响「同租户非属主非管理员的成员以绝对路径读取私有 Agent workspace」这一条路径；正常的面板浏览（租户共享根）、工件下载（共享 Agent）、平台根浏览均不受影响。上线前用 `tests/test_platform_file_browsing.py`、`tests/test_console_workspace_transport.py`、`tests/test_tenant_read_scoping.py` 回归确认。

## Verification

1. RED：新增跨成员私有 Agent 用例，实测在实现前失败（`resolve` 返回 200 且含 `preview_url`、`/api/file` 返回私有字节）。
2. GREEN：同一用例在实现后返回 403/404 且不含文件内容/元数据。
3. 回归：属主本人与 `tenant_admin` 仍可读；共享 Agent、租户共享根、平台根（平台管理员）行为不变；归属不唯一时拒绝。
4. `openspec validate fix-private-agent-file-scope --strict` 通过。
