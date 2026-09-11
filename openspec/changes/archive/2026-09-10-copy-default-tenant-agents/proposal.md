## Why

**一、租户维护无法给租户配智能体。** 平台管理员的租户编辑器目前只有「基本信息」「模型授权」「工具授权」「租户管理」四个标签，没有任何智能体维度；后端也只有全局的 `/api/agents`（注册表管理），没有任何租户→智能体的配置入口。默认租户之外的租户因此既看不到、也拿不到智能体。

**二、智能体是全局实体，绑定严格一对一。** 智能体定义在全局 roster（`team.json`）中，有稳定 id、名称、工作区目录与数字员工配置；`agent_bindings` 表把 `agent_id` 唯一映射到一个 `tenant_id`，`IdentityService.bind_agent()` 明确拒绝跨租户改绑。因此"让新租户用上默认租户的智能体"不能靠共享绑定实现，只能**克隆出独立实体再绑定**。

**三、已有可复用的克隆地基。** `AgentAdminService.create_agent(clone_from=...)` 已能新建智能体 id + 独立工作区，并只拷贝人设核心文件（`AGENT.md`/`USER.md`/`RULE.md`/`BOOTSTRAP.md`），刻意不拷记忆、会话库、凭据与共享技能/知识目录；roster 写入有 `revision` 冲突保护，失败会回滚新建目录。缺的只是"跨租户编排 + 幂等记录 + 入口"。

**四、默认租户有明确的数据锚点。** 现有注册表智能体全部通过 `register_default_tenancy` 绑定在 bootstrap 创建的租户下，且全局默认智能体 `registry.default_agent_id` 的绑定指向该租户。以此作为"默认租户"的解析依据，不引入新的 `is_default` 标记。

## What Changes

- **新增租户智能体配置入口**：租户编辑器新增「智能体」标签（插在「工具授权」与「租户管理」之间），只读列出该租户当前绑定的智能体（名称 / id / 是否启用 / 是否默认），并列出默认租户的**可复制候选供勾选**（含"已有克隆"标记），勾选后提交复制并回显结果（新增 / 跳过 / 失败明细）。未勾选即提交被拒；租户尚未创建时该标签呈说明态且不发出写入。
- **新增平台接口**：`GET /api/platform/tenants/<id>/agents`（目标租户绑定智能体 + 来源候选取并集，只读投影，不含 workspace、凭据等敏感字段）与 `POST /api/platform/tenants/<id>/agents`（`action=copy_from_default` + 显式 `source_agent_ids` 选中集合，要求近期密码校验）。
- **复制以显式选中集合为准**：请求 MUST 携带非空的来源智能体标识集合（空集合被拒，MUST NOT 退化为全量复制），且每个标识 MUST 属于来源租户的可复制候选，否则整体拒绝、不产生部分写入。
- **克隆语义为"独立智能体"**：为每个源智能体新建全局唯一的智能体 id、在目标租户自己的共享根下落独立工作区，并复制人设核心文件与配置属性（名称、描述、头像、模型/bot_type、技能与知识**选择**、数字员工字段、工具白/黑名单）。MUST NOT 复制记忆、会话、凭据、技能/知识实体文件与渠道绑定。
- **幂等与来源映射**：`agent_bindings` 新增 `cloned_from_agent_id` 列及部分唯一索引 `(tenant_id, cloned_from_agent_id)`；重复执行按该映射跳过已复制的源智能体，只补缺。MUST NOT 依赖 id/名称命名约定做幂等。
- **默认智能体承接**：目标租户此前**没有任何智能体**时，把"源默认智能体的克隆"设为其默认智能体；目标已有智能体或已配置默认时 MUST NOT 改动。
- **失败可恢复**：逐智能体独立处理并做补偿（删除刚建工作区、回滚 roster 条目）；返回部分结果，重复执行续跑。MUST NOT 因单个智能体失败而静默丢弃已成功项或误报整体成功。
- **授权与审计**：仅平台管理员可调用，写入要求近期密码校验；成功与拒绝均按既有身份审计策略落审计，脱敏、不含密钥。成功后热重载 Agent 运行时，新智能体无需重启即可用。
- **规范同步**：把 `tenant-management` 的"四个标签"改为五个标签并补充「智能体」标签契约。

### 与 PRD 的关系

- 需求基线按 `openspec/config.yaml` 指向的 PRD-01～12 v1.2。仓库所称 PRD 原文沿袭既有 change 的记录**当前仍缺失**，故本次行为基线取自 `openspec/specs/` 下既有规范（`tenant-management`、`tenant-resource-isolation`、`agent-workbench`）与已交付的 Agent Registry / 身份域代码。PRD 原文恢复后 MUST 复核两处口径：① 租户智能体是否要求"复制即快照、允许后续各自演化"；② 是否要求目标租户默认智能体的唯一性。
- **数据唯一归属**：来源映射（`cloned_from_agent_id`）与智能体→租户绑定归属 `identity.db` 的 `agent_bindings`，由 `IdentityService` 写入并审计；智能体实体与工作区归属全局 roster（`team.json`）与各自的 Agent workspace，由 `AgentAdminService` 写入。本 change MUST NOT 在身份库复制人设文件内容，也 MUST NOT 在 roster 写入凭据。
- **跨 change 依赖**：不强依赖在途 change；`open-database-runtime` 的 Agent 绑定与隔离需求是本 change 的前置（已在其 4.x/7.x 交付与 `tenant-resource-isolation` 主规范中体现）。本 change MUST NOT 修改 `tenant-resource-isolation`、`agent-workbench` 的既有需求，以免覆盖其 delta。

## Capabilities

### New Capabilities

- `tenant-agent-provisioning`: 平台管理员在租户维护中把"默认租户的智能体"克隆到指定租户的能力——源租户解析、可复制候选读取、以显式勾选集合为准的复制、独立克隆语义、复制内容边界（含明确不复制项）、来源映射与幂等跳过、目标默认智能体承接、失败补偿与部分结果、授权与审计、运行时热重载。

### Modified Capabilities

- `tenant-management`: 租户编辑器的固定标签集合由四个改为五个（「智能体」插在「工具授权」与「租户管理」之间），并补充该标签的只读列表 + 候选勾选 + 复制动作契约、未勾选即提交被拒、未创建租户时的说明态，以及相应的平台接口在编辑器内的一次提交语义。

## Impact

- **代码**
  - `agent/tenant_provisioning.py`（新增）：跨租户克隆编排（源解析、id 生成、内容复制、roster 写入、绑定与映射、补偿、热重载）。
  - `agent/admin.py`：抽出/复用 `_bootstrap_workspace`、`_clone_persona`、`_seed_name`、`_knowledge_mode` 等；新增可供编排调用的克隆入口（不改既有 `create_agent` 行为）。
  - `auth/service.py`：`bind_agent` 支持记录 `cloned_from_agent_id`；新增按租户/来源查询绑定；复制操作的审计。
  - `auth/store.py`：新增版本化迁移（`agent_bindings` 加列 + 部分唯一索引）。
  - `channel/web/admin_handlers.py`、`auth/http_policy.py`：新增 `/api/platform/tenants/<id>/agents` 的 GET/POST 策略与近期密码校验。
  - `channel/web/web_channel.py`：注册路由；成功后调用既有 `_reload_agent_runtime`。
  - `channel/web/static/js/identity-admin.js`、`static/css/console.css`：新增「智能体」标签、列表、复制按钮与三语 i18n。
- **API**：新增两个平台端点（GET 返回目标租户绑定智能体 + 来源候选，POST 携带 `source_agent_ids` 选中集合）；不改动 `/api/agents` 的既有契约（含 `clone_from` 行为）。
- **数据**：`identity.db` 一次版本化迁移（加列 + 部分唯一索引），`team.json` 结构不变。
- **安全**：MUST NOT 跨租户读取源智能体的记忆、会话、凭据或共享实体文件；MUST NOT 返回 workspace/宿主路径；越权返回 403 并审计；写入要求近期密码。
- **兼容**：默认租户既有智能体与其工作区 MUST 保持不变；无绑定的 legacy 形态 MUST 表现为"无智能体可复制"而不是报错。
- **测试**：单测（源解析、候选投影与已复制标记、选中集合校验含空集合与越界标识、id 生成与冲突、内容边界含不复制项、幂等跳过、默认承接、失败补偿、越权 403、recent_password）、迁移测试（旧库升级 + 幂等重跑）、前端 cjs（标签渲染/候选勾选/复制调用/未勾选不发请求/结果提示/未创建禁用）、端到端（复制后目标租户可用且源租户不受影响）、`evidence.md`。
- **feature flag**：不引入运行开关；能力随代码发布生效。仅在 database 身份模式下提供（legacy 模式无租户绑定，端点不启用）。
