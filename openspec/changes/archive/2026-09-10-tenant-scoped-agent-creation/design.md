## Context

动机与范围见 `proposal.md`；行为契约见 `specs/agent-workbench/spec.md` 与 `specs/role-resource-authorization/spec.md`。以下是实现必须面对的现状约束（均已核对代码与运行实例）：

- 智能体是全局实体：`AgentRegistry` 由 roster `team.json` 驱动；`agent_bindings(agent_id PRIMARY KEY, tenant_id, ...)` 把 `agent_id` 唯一映射到一个 `tenant_id`（`auth/store.py`）。
- 读写不对称是根因：写路径 `_agent_admin_service()`（`web_channel.py`）恒用 `get_data_root()` 派生实例根、且不写绑定；读路径 `_tenant_agents_projection` 按 `_tenant_ids_for_context`（= `tenant_agent_ids`）过滤。二者用不同口径，导致「创建成功但不可见」。
- 内置 `tenant_admin` 角色不携带 `agent:*` grant：实测 `resource_ids_for(agent, read, agent.read)` 与 `check_resource_action(agent.use)` 对租户管理员均返回空/False，因此 `_tenant_agents_projection` 的 `allowed_agent_ids` 过滤会把该租户**全部**智能体滤掉。
- 既有放行先例：`_require_agent_create` 与 `_require_chat_use` 已对 `ctx.is_tenant_admin` 放行；`_require_read_permission` 只对平台管理员放行，并明确「管理身份不得绕过自定义角色的功能权限」。
- 租户根互不包含由 `validate_tenant_shared_root` 守护；`AgentAdminService.create_agent` 的缺省 workspace 是 `<instance root>/agents/<id>`，并以 `_reject_overlapping_workspace` 拒绝嵌套。

## Goals / Non-Goals

**Goals:**

- 让「租户管理员创建智能体」在其自己的控制台里可创建、可见、可对话、可管理。
- 租户智能体的工作区归属与该租户的共享根一致，不落进实例根。
- 隔离边界可验收：跨租户的智能体既不可见也不可编辑。

**Non-Goals:**

- 不做平台管理员向租户克隆智能体（属 `copy-default-tenant-agents`）。
- 不改 roster 的全局实体模型（仍为 `team.json` 全局唯一 id）。
- 不放宽 legacy 模式，不新增运行开关。
- 不把逐资源 grant 从成员路径移除（grant 仍是把访问扩给非管理员成员的唯一手段）。

## Decisions

### D1 工作区落在租户共享根，显式 workspace 优先

`_tenant_agent_workspace(ctx, agent_id)` 返回 `<tenant.shared_root>/agents/<agent_id>`；租户无已解析共享根时返回 None，交回 `AgentAdminService` 的实例根默认（保持 legacy 单租户形态）。

- 备选：在 `AgentAdminService` 内按当前身份推导根。否——该服务不应依赖 identity 域，且它同时服务 legacy 路径。
- 备选：把工作区也留在实例根、仅补绑定。否——会保留「租户文件混入共享实例根」的隔离缺陷。

### D2 创建后立即绑定，首个智能体承接默认

`_adopt_created_agent_for_tenant(ctx, agent_id)` 先记 `tenant_agent_ids` 是否为空，再 `bind_agent(tenant_id, agent_id, private_owner_user_id=ctx.user_id)`；若此前无任何绑定且租户未配置默认，则调用 `appoint_tenant_default_agent` 承接默认。

- 备选：由平台管理员事后手工勾选绑定。否——租户自建对象却需外部补授权，与该动作的语义不符，也正是本次缺陷。
- 备选：每次创建都把新智能体设为默认。否——会悄悄改变用户已选定的默认。

### D3 租户绑定是租户管理员权限的边界，而非逐资源 grant

新增 `_tenant_admin_owns_agent(ctx, agent_id)`：`ctx.is_tenant_admin` 且 `agent_id ∈ tenant_agent_ids(ctx.tenant_id)`。该判据统一用于三处，使三处口径不分叉：

1. `_tenant_agents_projection`：租户管理员跳过 `allowed_agent_ids` 过滤（`visible` 已把它限制在本租户绑定内）。
2. `_workbench_chat_readiness`：`agent.use` 判定加入同一判据，保证卡片承诺的 `can_chat` 与发送路径一致。
3. `_require_agent_action`：命中判据即放行；未命中仍走原 `_require_resource_action`，因此对**其他租户**绑定的智能体仍返回 403。

- 备选：让创建路径给创建者角色补 `agent:read`/`agent:use` grant。否——内置 `tenant_admin` 还缺 `agent.edit` 功能权限与 `agent.use`，补 grant 仍无法编辑/对话，无法闭环；且以副作用改写角色配置语义更隐晦。
- 备选：把 `resource_ids_for` 整体改成「无任何 grant 时回退不受限」。否——那是全局授权语义变更，波及模型/技能/工具等所有资源类型，超出本缺陷范围。

### D4 id 先校验，路径后拼接

租户作用域路径由 agent id 拼出，因此在创建前用与 `AgentProfile` 相同的标识规则（`agent.registry._AGENT_ID_RE`，单一事实来源，避免与 `AgentProfile` 校验漂移）校验；非法即返回错误且不落盘。原文依赖 `AgentProfile` 在 `_bootstrap_workspace` 之后才拒绝，会先创建目录再回滚。

### D5 `set_tenant_default_agent` 拆门禁与实现，新增租户侧入口

原方法为平台管理员专用（属租户编辑器批量提交链）。拆出 `_appoint_tenant_default_agent`（无门禁、只校验租户存在且智能体已绑定该租户）后：`set_tenant_default_agent` 保持平台管理员门禁不变；新增 `appoint_tenant_default_agent` 走 `_require_tenant_admin`（平台管理员或该租户管理员）。两者都必须通过「已绑定该租户」这一硬校验，因此租户默认永远不可能指向其他租户的智能体。

## Risks / Trade-offs

- **授权语义放宽的范围**：仅放宽「租户管理员 × 本租户绑定的 agent」，成员路径与跨租户路径不变；以 `agent_bindings` 为唯一判据，未命中即回落到原严格检查。已用跨租户用例（不可见 + 403）钉住。
- **roster 仍为全局**：`team.json` 会同时包含各租户的智能体（沿用既有全局实体模型）。隔离由「工作区路径 + 绑定」承担，不由 roster 文件承担。
- **已有租户的存量数据**：本次不回溯修复历史落在实例根的租户智能体；存量如需归位，应由运维显式迁移。

## Migration Plan

无 schema 变更、无数据迁移。已存在的「已创建但未绑定」的智能体不在本 change 自动收养；如确需归位，另行显式迁移（移动工作区 + 补绑定 + 归还实例根）。

## Open Questions

- 租户管理员的跨租户读取是否还需要在平台侧提供只读审计视图（当前：不做，超出本缺陷范围）。
