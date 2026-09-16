# 1.2 授权矩阵基线（Stage 1）

本文件是 `enable-member-personal-console` 任务 1.2 的证据产物：**当前代码的真实行为基线**（不是目标行为）。
目标行为见本 change 的 `specs/rbac-authorization`、`specs/user-personal-context`、`specs/tenant-resource-isolation` 等增量规范。

标注约定：
- ✅ 允许 · ❌ 拒绝 · ⚠️ 允许但仅在特定条件下（见备注） · ❓ 尚未确认（需由 1.4 旁路清单补齐）

## 1. 参与者类别

| 代号 | 参与者 | 判定依据 |
| --- | --- | --- |
| A1 | 当前租户**本人**（有效成员、资源 owner） | `agent_bindings.private_owner_user_id == user_id` 且 `tenant_id` 匹配 |
| A2 | **同租户他人**（有效成员、非 owner） | 有效 membership，但不是该私有资源 owner |
| A3 | **本租户 tenant_admin**（非 owner） | `auth/policy.py` `TENANT_ADMIN_CODE` 资格；注意其**默认权限集** |
| A4 | **有效成员平台管理员** | `is_platform_admin_user(user_id)` 为真，且存在有效 membership |
| A5 | **跨租户成员** | 另一个 tenant 的有效成员 |
| A6 | **失效成员 / 停用账号** | 无有效 membership，或 user/membership 非 active |

## 2. 操作类别

| 代号 | 操作类别 | 范围 |
| --- | --- | --- |
| O1 | 私有 Agent 读/用 | 他人或本人的私有智能体配置、会话 |
| O2 | 私有 Agent 配置/启停/删除 | 编辑配置、核心文件、启停、调试、删除 |
| O3 | 个人记忆 读/写/清空 | `scope=user` 记忆内容与索引 |
| O4 | 个人渠道 配置/凭证 | 个人渠道实例、凭证版本、绑定挑战 |
| O5 | 私有文件 / 预览 / 下载 | 私有工作区实际文件路径消费 |
| O6 | 私有 own 知识 读/写 | 私有知识库读取与写入 |
| O7 | 私有会话 / runs | 私有智能体的会话历史与执行记录 |
| O8 | **公共操作**（共享 Agent、租户知识、工具/技能目录、租户渠道） | 租户共享资源的既有授权 |
| O9 | **治理元数据**（归属、数量、状态、用量、脱敏审计） | 管理投影，不含正文/凭证 |

## 3. 当前基线矩阵

| 操作 | A1 本人 | A2 同租户他人 | A3 tenant_admin | A4 平台管理员 | A5 跨租户 | A6 失效成员 |
| --- | --- | --- | --- | --- | --- | --- |
| O1 私有 Agent 读/用 | ✅ | ❌ | ✅ **默认集含 `agent.read`/`agent.use`** | ⚠️ **all 直通** | ❌ | ❌ |
| O2 私有 Agent 配置/启停/删除 | ❌ **owner 无写权** | ❌ | ✅ **默认集含 `agent.edit`/`agent.enable`** | ⚠️ **all 直通** | ❌ | ❌ |
| O3 个人记忆 读/写/清空 | ⚠️ | ❌ | ❌ **正文按 owner 隔离，无旁路证据** | ⚠️ **all 直通（记忆路径未复核）** | ❌ | ❌ |
| O4 个人渠道 配置/凭证 | ⚠️ **凭证尚无用户 owner** | ❌ | ✅ **`_is_control` 全权（含明文 resolve）** | ✅ **`_is_control` 全权** | ❌ | ❌ |
| O5 私有文件/预览/下载 | ✅ | ❌ | ✅ **`_db_path_owner_forbidden:1114` 豁免（含正文）** | ❌ **非旁路（已核实）** | ❌ | ❌ |
| O6 私有 own 知识 读/写 | ⚠️ **owner 无写权** | ❌ | ✅ **`:1114` 豁免** | ✅ **all 直通** | ❌ | ❌ |
| O7 私有会话/runs | ✅ **owner 校验无管理员旁路** | ❌ | ❌ **无管理员旁路** | ❌ **无管理员旁路** | ❌ | ❌ |
| O8 公共操作 | 按授权 | 按授权 | ✅（默认集） | ✅ all | ❌ | ❌ |
| O9 治理元数据 | n/a | ❌ | ⚠️ **投影含私有配置（超出治理元数据）** | ⚠️ **同左** | ❌ | ❌ |

## 4. 已核实的代码事实（file:line）

### 4.1 统一授权入口

| 入口 | 位置 | 语义 |
| --- | --- | --- |
| `authorization_mode(user_id, tenant_id)` | `auth/service.py:1237` | 平台管理员返回 `"all"`，否则 `"role"`。**每次重算，不读前端、不缓存** |
| `check_resource_action(user_id, tenant_id, kind, resource_id, action, *, permission=None)` | `auth/service.py:1253` | 单对象动作判定；**先判 `all`，再判 membership，最后才判 owner** |
| `resource_ids_for(user_id, tenant_id, kind, action, permission=None)` | `auth/service.py:1424` | 集合投影；平台管理员返回 `None`（= 不受限，调用方投影全目录） |
| `is_private_agent_owner(tenant_id, user_id, agent_id)` | `auth/service.py:1408` | 按绑定主键单行查，不缓存 |
| `private_agent_ids(tenant_id, user_id)` | `auth/service.py:1390` | owner 私有 Agent 集合，来源 `agent_bindings.private_owner_user_id` |

### 4.2 关键的**旁路顺序**问题（本 change 任务 3.2 的靶点）

```1275:1288:auth/service.py
        if kind not in RESOURCE_ACTIONS or action not in RESOURCE_ACTIONS[kind]:
            return False
        if self.authorization_mode(user_id, tenant_id) == "all":
            return True
        membership = self._membership(user_id, tenant_id)
        if not membership:
            return False
        if permission is not None:
            if permission not in self._permissions_for_membership(membership["id"]):
                return False
        if (kind == "agent" and action in PRIVATE_AGENT_OWNER_ACTIONS
                and resource_id.startswith("agent:")
                and self.is_private_agent_owner(
                    tenant_id, user_id, resource_id[len("agent:"):])):
            return True
```

平台 `all` 在 **membership 校验之前** 返回 True，且 owner 判定在其后 —— 即平台管理员对任意已知 kind/action 直通，
不经任何归属检查。`resource_ids_for` 同理（`:1439` 返回 `None` 哨兵）。

### 4.3 owner 动作集合**尚未覆盖写**（本 change 任务 3.1 的靶点）

```375:375:auth/policy.py
PRIVATE_AGENT_OWNER_ACTIONS: Tuple[str, ...] = ("read", "use")
```

`fix-private-agent-owner-reachability` 刻意把所有权收窄为「可达 ≠ 可改」：owner 由所有权获得 `read`/`use`，
`edit`/`enable` 仍**必须**有逐对象 grant。本 change 需要扩展到配置/调试/启停及自建对象删除。

### 4.4 tenant_admin 默认权限集（旁路的权限面）

`auth/policy.py:212` `TENANT_ADMIN_DEFAULT_PERMISSIONS` 是逐项写死的超集，其中与本矩阵相关的高危项：

| 权限 | 影响的操作类别 | 备注 |
| --- | --- | --- |
| `agent.edit` | O2 | tenant_admin 具备编辑任意（含他人私有）Agent 的**功能权限** |
| `agent.enable` | O2 | 同上，启停 |
| `memory.read` | O3 | tenant_admin 持有「读取个人记忆」功能权限 |
| `knowledge.write` | O6 | tenant_admin 持有知识写权限（与在途 `restrict-knowledge-write-authorization` 有交集） |
| `agent.read` / `agent.use` | O1 | 功能层可达 |

注意 `MEMBER_DEFAULT_PERMISSIONS`（`auth/policy.py:186`）成员**已含** `agent.edit`，但那是租户共享 Agent 语境；
私有对象必须在归属层先被拦下（见 4.2 的判定顺序）。

### 4.5 已有的「窄豁免」范式（可复用的好模式）

这两个方法展示了本 change 应采用的收窄写法：功能权限 + 有效成员 + 作用域限定，三者同时成立才放行，且不缓存。

| 方法 | 位置 | 收窄条件 |
| --- | --- | --- |
| `tenant_admin_may_execute_tool(user_id, tenant_id, resource_id, agent_id=None)` | `auth/service.py:1295` | 本租户 active `tenant_admin` + `tool.execute` + 工具确属本租户（MCP 分支还要求 `agent_id` 是本租户绑定） |
| `personal_memory_tool_may_execute(user_id, tenant_id, tool_name, arguments=None)` | `auth/service.py:1345` | 工具名在固定集 `PERSONAL_MEMORY_TOOLS` + active 成员 + 同时持有 `tool.execute` 与 `memory.read` + `memory_add` 必须留在**本人作用域**（`_memory_write_stays_own_scope`） |

`personal_memory_tool_may_execute` 里的「写必须留在本人作用域」是本 change 记忆写边界的现成先例。

### 4.6 私有 own 知识写的 tenant_admin 旁路（已核实）

写闸门先放行管理员，再由 owner 谓词**再次豁免**管理员，导致 owner 比较根本不会执行：

```318:337:channel/web/web_channel.py
def _require_knowledge_write(ctx: "Optional[RequestContext]") -> None:
    ...
    if ctx is None:
        return
    if ctx.is_platform_admin or ctx.is_tenant_admin:
        return
    _require_read_permission(ctx, "knowledge.write")
```

```1104:1117:channel/web/web_channel.py
    if ctx is None or not getattr(ctx, "tenant_id", None) or not agent_id:
        return False
    from auth.service import get_identity_service
    binding = get_identity_service().get_agent_binding(agent_id)
    if not binding:
        return False
    owner = binding.get("private_owner_user_id")
    if not owner:
        # tenant-shared asset: any permission-holder of the tenant may read.
        return False
    if getattr(ctx, "is_tenant_admin", False):
        return False          # ← 管理员在此被豁免，owner 比较永不执行
    return owner != getattr(ctx, "user_id", None)
```

handler 的调用顺序也帮不上忙 —— 写闸门在前、owner 闸门在后，而后者对管理员恒为放行：

```
11053:11055:channel/web/web_channel.py
                _require_knowledge_write(ctx)
                agent_id = _require_tenant_agent_binding(ctx, _request_agent_id(body))
                _require_private_owner(ctx, agent_id)
```

**结论**：仅调整 handler 调用顺序**无法**修复；必须改 `web_channel.py:1114` 这一行（或为写路径引入独立的 owner 谓词）。
这与本 change 任务 3.5 直接相关，且与在途 change 的 delta 存在**意图冲突**（后者刻意允许 tenant_admin 写他人 own 库），见 1.1。

## 5. 基线结论（Stage 1 输出）

1. **平台管理员 `all` 是最主要的私有内容旁路**，且它在判定顺序上位于归属检查之前 —— 不能靠「加一个 owner 判断」修好，必须调整为 owner 优先（任务 3.2）。
2. **owner 写权缺失是第二个缺口**：`PRIVATE_AGENT_OWNER_ACTIONS` 只含 `read`/`use`，成员无法维护本人私有 Agent（任务 3.1）。
3. **tenant_admin 的默认权限集在功能层就已覆盖 `agent.edit`/`agent.enable`/`memory.read`/`knowledge.write`**，因此「管理员不能访问私有内容」不能只靠菜单或功能权限实现，必须在**归属层**拦截并在其前置于管理员旁路。
4. **平台管理员并非所有路径的旁路**：在**文件/工作区内容**路径上，`_db_path_owner_forbidden` 从不检查 `is_platform_admin`（已逐行核实 `web_channel.py:1104-1117`），`is_platform_admin` 只影响平台根。内容级唯一旁路是 `is_tenant_admin`（`:1114`）。因此 O5 的 A4 列记为 ❌ 而非 ⚠️。
5. **预览令牌缺归属复验**：`PreviewHandler.GET`（`:3672`）只有路径包含校验、无身份与 owner 复验（`:3702-3707` 已核实），旧私有令牌在归属收紧后仍可用；这是任务 3.4 的必办项。
6. **矩阵已定稿**：22 处旁路已逐条复核（见 `1-4-admin-owner-bypasses.md`）。要点：
   - **O7 会话/runs 无管理员旁路** —— `_require_owned_session` 只比 `sessions.owner`，不看管理员状态；勿假设「管理员处处能穿透」。
   - **O4 个人凭证尚不存在** —— `credentials` 表只有 `tenant_id` + `created_by`、**无 owner 列**（`auth/store.py:305-317` 已核实），
     故当前不存在「个人凭证」这一对象，`_is_control` 的租户级全权就是待新建的接缝。
   - **O9 治理投影越界** —— 管理员投影当前含 persona/model/bot_type 等私有配置，超出 spec 允许的「归属、数量、状态与用量」。
7. **附带发现（非管理员旁路）**：`SessionSettingsHandler`（`web_channel.py:9855/9872`）只有 `_db_scope()`、**无会话归属校验**，
   任何本租户成员可用任意 `session_id` 读写会话偏好；归入任务 3.x。

> 状态：1.2 **完成**。
