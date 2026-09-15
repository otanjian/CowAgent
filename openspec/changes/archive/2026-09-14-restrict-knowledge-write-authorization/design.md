## Context

现状（`open-tenant-knowledge-console` 已归档）：

- 写门禁是 `channel/web/web_channel.py::_require_knowledge_write(ctx)`：平台管理员或本租户 `tenant_admin` 直接放行，否则要求 `knowledge.write`。它与 `agent_id` 无关，对租户级与私有智能体一视同仁。
- 读路径已按智能体作用域：`_require_tenant_agent_binding` + `_require_private_owner` + `_knowledge_workspace_root(agent_id)`，后者把数据根交给 `state_dir` 的「按存在即独立」规则解析。
- 数据根归属判定已有权威实现：`AgentAdminService._shared_knowledge_base()`（按 `state_dir.shared_root()` 解析共享库基准，与运行时同源）与 `_knowledge_mode_of(profile, shared_base)`（own ⟺ 工作区下的实体 `knowledge/` 目录且不是共享库本身）。
- 私有智能体只由 `PersonalAssistantProvisioner` 产生：`clone_agent(...)` 继承来源的 `knowledge_mode`，而租户模板通常是 shared，于是个人助理没有自有 `knowledge/`，运行时读到的是租户共享库。
- 内置角色：`member` 有 `knowledge.read` 无 `knowledge.write`；`tenant_admin` 由 `TENANT_ADMIN_DEFAULT_PERMISSIONS` 显式枚举（含 `knowledge.write`）。`PRIVATE_AGENT_OWNER_ACTIONS = ("read", "use")`，owner 不能改自己的智能体配置（含知识库模式）。

## Goals / Non-Goals

**Goals:**

- 租户级智能体的知识库只有平台管理员与本租户租户管理员能写。
- 私有智能体的 owner 能维护其自有知识库；且任何成员都不能借私有智能体写入租户共享库。
- 授权判定与数据根归属、与读取路径同源，不引入第二套事实。
- 控制台知识库页的写入口按所选智能体呈现，不再给出必然 403 的按钮。

**Non-Goals:**

- 不改读取授权（仍 `knowledge.read` + 私有归属裁剪）。
- 不新增知识库资源级 grant，不定义条目级 `knowledge_ids` 的读写语义。
- 不回填存量私有智能体的知识库（保持现状）。
- 不改变智能体知识库模式切换（`set_knowledge_mode`）的授权（仍属 `agent.edit`）。

## Decisions

### 1. 写授权按「数据根归属 + 智能体归属」判定，而非权限

```python
def _knowledge_write_authorized(ctx, agent_id) -> bool:
    if ctx is None:                       # legacy 身份模式无写门禁
        return True
    svc = get_identity_service()
    binding = svc.get_agent_binding(agent_id)
    if not binding or binding["tenant_id"] != ctx.tenant_id:
        return False                      # 跨租户：调用方已由绑定校验拒绝（404）
    if ctx.is_platform_admin or ctx.is_tenant_admin:
        return True
    # 普通成员：仅「自己拥有的私有智能体」且「写入落在其自有 knowledge/」
    if binding.get("private_owner_user_id") != ctx.user_id:
        return False
    return _knowledge_mode_of_agent(agent_id) == "own"
```

`_knowledge_mode_of_agent` 复用 `AgentAdminService._shared_knowledge_base()` + `_knowledge_mode_of()`。`own` 模式意味着数据根是该智能体工作区下的实体 `knowledge/`；`shared` 模式意味着数据根就是租户共享库（符号链接/不存在回落）。于是「按数据根判定」被一条 `knowledge_mode == "own"` 精确覆盖，无需重复解析路径。

替代方案：把规则写成「owner 可写私有智能体」而不管数据根。被否——私有智能体若为 shared 模式，其 owner 会直接改写租户共享库，与「只有租户管理员维护共享库」冲突。

### 2. 移除 `knowledge.write` 而不是保留为空转权限

按严格口径它不再授权任何写入路径，保留只会让角色编辑器展示一个无效开关，并在「保存既有自定义角色」时因目录不再包含该 id 而暴露脏数据。移除方式：

- `auth/policy.py`：从 `PERMISSION_CATALOG`、`PERMISSION_METADATA`、`TENANT_ADMIN_DEFAULT_PERMISSIONS` 删除。
- `auth/store.py`：新增 `_migration_15`，把每个角色 `permissions_json` 中的 `knowledge.write` 剥离并 `version+1`；只删这一个 id，其它权限原样保留。
- `channel/web/route_registry.py` 注释、`scripts/route-baseline.txt`、前端 `canWriteKnowledge` 与测试同步。

### 3. 写能力投影进 Agent 管理目录（方案 A）

`_tenant_agents_admin_projection` 为每个智能体增加 `can_write_knowledge`（同一判定函数），`/api/agents` → 前端 `agentCatalog` 天然携带；知识库页按所选智能体读取该布尔渲染写入口。这样规则只存在于服务端一处，UI 同步、无需额外请求；服务端仍是权威（直接 POST 仍会被 403）。

替代方案：只在 `/api/knowledge/list` 返回 `can_write`。被否——写入口在列表返回前无法确定，切换智能体时按钮闪烁，且与既有 `renderKnowledgeWriteAffordances()` 的开机时序冲突。

### 4. 私有智能体默认独立知识库

`PersonalAssistantProvisioner` 克隆时传 `knowledge_mode="own"`，使新私有智能体获得空的自有 `knowledge/`。这与 `user-personal-agent-provisioning` 既有「MUST NOT 复制来源的知识实体文件」一致（自有库为空）。存量不回填：它们仍是 shared 模式，owner 因此不可写——这是有意的保守选择，避免把共享库内容突然从用户视野中移除，也避免把共享内容复制进私人库。

### 5. 页面文案表达维护责任

`knowledge_shared_hint` 三语改为「租户级智能体的知识库由租户管理员维护；私有智能体的知识库由本人维护」语义，保留「按智能体共享/独立，可进入智能体管理设置」的既有信息。

## Risks / Trade-offs

- **成员失去唯一的写入入口**：一个没有私有智能体的普通成员在任何知识库上都不能写（此前若被授予 `knowledge.write` 尚可）。这是产品的有意收紧；需要写入能力的部署仍可通过把该成员设为 `tenant_admin`，或为其供应私有智能体实现。
- **存量私有智能体仍不可写**：直到租户管理员把该智能体切为 own（或在供应流程重建）。已按用户确认不做回填。
- **迁移剥离权限**：会把自定义角色中已授予的 `knowledge.write` 一并删除。由于该权限已无任何授权效果，删除只影响目录展示，不影响其它权限；迁移只处理单个 id，且幂等。
- **投影成本**：`/api/agents` 每个智能体多一次绑定查询与一次 realpath 比较。数据量是租户级智能体数，可接受；判定为纯读、无副作用。
- **跨租户克隆视角**：平台管理员带租户身份读他租户 Agent 目录时，`can_write_knowledge` 按其平台资格返回 true，与其实际写权限一致（平台管理员写入不受租户边界限制但需有效成员资格），无额外暴露。
