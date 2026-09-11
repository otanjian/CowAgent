## Why

**一、租户管理员创建的智能体对自己不可见。** 控制台「智能体管理」向租户管理员提供「创建智能体」，`_require_agent_create` 也显式放行租户管理员（`channel/web/web_channel.py`）。但写入路径 `_agent_admin_service()` 恒用 `get_data_root()` 派生的实例根，完全不区分租户：新建智能体写进全局 roster、落在**实例根**目录，且**从不写 `agent_bindings`**。读取路径 `_tenant_agents_projection` 却按租户绑定过滤（`tenant_agent_ids`），于是同一租户刚创建的智能体在其列表里不存在——用户视角就是「创建没反应」。

**二、租户隔离边界被绕过。** 租户智能体的工作区应落在该租户自己的共享根下（`<shared_root>/agents/<id>`，见 `copy-default-tenant-agents` 的 D3）。落在实例根意味着一个租户的智能体文件与共享资产库、其他租户的智能体混在同一个目录下，破坏租户数据归属。

**三、租户管理员对自己租户的智能体没有任何管理权。** `_tenant_agents_projection`、`_workbench_chat_readiness` 与 `_require_agent_action` 都要求逐资源 grant，而内置 `tenant_admin` 角色不携带任何 `agent:*` grant，导致租户管理员即便创建成功，也看不到、无法对话、无法编辑自己租户的智能体。

## What Changes

- **创建即租户作用域**：database 模式下租户管理员创建智能体时，其工作区 SHALL 落在该租户共享根下 `<shared_root>/agents/<id>`；客户端显式提交 `workspace` 时以客户端为准。租户无已解析共享根时沿用既有实例根默认。
- **创建即绑定**：创建成功后 SHALL 写入 `agent_bindings`，把新智能体绑定到创建者租户。该租户此前没有任何绑定智能体且未配置默认时，SHALL 同时将其设为其租户默认智能体；已有默认时 MUST NOT 改动。
- **租户绑定即隔离边界**：租户管理员 SHALL 无需逐资源 grant 即可读取、对话、编辑、归档、删除**其自己租户绑定的**智能体。对绑定到其他租户的智能体 MUST 仍然拒绝（403），不得因管理员身份跨租户放行；平台管理员行为不变。
- **id 先校验再落盘**：租户作用域路径由 agent id 拼出，创建前 SHALL 以与 `AgentProfile` 相同的标识规则校验 id，非法 id MUST 被拒绝且 MUST NOT 创建任何目录（含租户根之外的目录）。
- **legacy 模式不变**：`ctx is None`（单租户/legacy）时保持既有实例根默认与「无绑定」行为。
- **契约不变**：`/api/agents` 的请求/响应结构、roster `revision` 冲突语义及既有客户端依赖保持不变。

## Impact

- **代码**
  - `auth/service.py`：`set_tenant_default_agent` 拆分为「门禁 + 实现」；新增 `appoint_tenant_default_agent`（平台管理员或该租户管理员可调用，且仅接受已绑定该租户的智能体）。
  - `channel/web/web_channel.py`：新增 `_tenant_agent_workspace`、`_adopt_created_agent_for_tenant`、`_tenant_admin_owns_agent`；调整 `AgentsHandler.POST` 的 create 分支、`_tenant_agents_projection`、`_workbench_chat_readiness`、`_require_agent_action`。
- **API**：不新增端点、不改结构；`POST /api/agents`（`action=create`）在 database 模式下的**落盘位置**与**绑定副作用**发生改变。
- **数据**：`identity.db` 新增 `agent_bindings` 行与 `tenants.default_agent_id` 赋值；roster（`team.json`）仍是全局实体存储，隔离体现在工作区路径与绑定上。
- **安全**：租户管理员的智能体权限严格以 `agent_bindings` 为界，不放大到其他租户；创建路径的 id 先校验，避免以构造 id 逃出租户根。
- **兼容**：legacy 模式与非租户调用路径行为不变。
- **测试**：新增 `tests/test_tenant_agent_creation.py`（9 项），覆盖租户根工作区、实例根不落盘、绑定、可见性、首个默认、后续不改默认、非法 id、租户内编辑、跨租户不可见且不可编辑。

### 与 PRD 的关系

行为基线取自 `openspec/specs/` 既有规范（`agent-workbench`、`role-resource-authorization`、`resource-execution-authorization`）以及 `copy-default-tenant-agents` 对租户智能体工作区归属的决定（`<target_tenant.shared_root>/agents/<new_id>`）。本 change 不修改 `tenant-resource-isolation` 的既有需求，只把「租户自建智能体」补齐到同一归属与授权口径；跨 change 依赖为 `open-database-runtime` 已交付的租户绑定与隔离能力。
