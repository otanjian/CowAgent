## Why

CowAgent 已有多智能体名册（概况 / 技能勾选 / 核心文件 / 共享·独立知识库），但缺少 OneAgent「数字员工」的核心能力：岗位化身份、知识/技能/SOP/工具四类资源装配、场景继承、运行时工具真裁剪，以及员工级定时任务与记忆/对话归属视图。业务侧需要把智能体升级为可配置、可授权、可运营的数字员工，而不是另起第二套名册。

## What Changes

- 在现有 `AgentProfile` 上演进数字员工能力（方案 A）：扩展岗位化字段、场景绑定、资源绑定与可见性接入点；**不**平行引入 `employees.json` / `agent/employee` 双名册。
- 智能体管理页签扩展：概况（职位/分类/标签/问候/人设摘要/关联场景）、能力（知识条目、技能、SOP、工具 allow/deny）、核心文件保留；新增「任务」页签（本智能体定时任务）；记忆/对话归属视图列入后续阶段。
- 工作台智能体卡片展示岗位化信息（职位、分类、标签），仍只读使用、不承载编辑器。
- 会话激活/构建时合成员工上下文：核心文件 + 人设摘要/问候策略；`tools_allowlist` / `tools_denylist` **在 ToolManager 做硬过滤**；技能/知识/SOP 按绑定装配；空字段从关联场景继承。
- Phase 1 以配置模型 + 运行时真裁剪 + 工作台展示闭环为主；内置岗位种子、完整可见性 UI、per-agent 运行参数覆盖、数据岗 SAP 工作台接通列入后续阶段。
- OneAgent 仅作参考：不复制运行时配置、凭据、客户数据或整文件覆盖；不引入「普通/深度代理」二分。

## Capabilities

### New Capabilities

- `agent-digital-employee-profile`：智能体岗位化档案（职位、分类、标签、问候、人设摘要）、场景绑定与场景字段继承规则、管理页概况扩展。
- `agent-capability-bindings`：按智能体绑定知识条目、技能、SOP、工具白/黑名单的持久化与管理 UI（能力页签）。
- `agent-runtime-capability-enforcement`：会话构建时注入员工上下文，并对工具/技能/知识/SOP 绑定做运行时真裁剪（含 ToolManager 过滤）。
- `agent-employee-tasks`：智能体详情内的定时任务列表与 CRUD（任务归属 `agent_id`）。

### Modified Capabilities

- `agent-workbench`：工作台卡片补充职位/分类/标签等展示字段；列表投影仍不返回工作空间路径与凭据。

## Impact

- **需求依据**：用户确认的「智能体演进为数字员工核心能力」改造建议（方案 A）。项目配置引用 PRD-01～12 v1.2；当前 `doc/优化规划/` 若缺 PRD 原文，本 change 为能力增量，恢复原文后核对映射。
- **数据唯一归属**：智能体配置继续由 `AgentRegistry` / `AgentAdminService` / 现有 `agents` 配置拥有；场景配置归属 `scenes/`（`port-scene-applications`）；定时任务归属现有 scheduler 存储，仅增加 `agent_id` 归属过滤；不新增平行员工 ID 空间。
- **接入点**：`agent/registry.py`、`agent/admin.py`、`agent/tools/tool_manager.py`（或等价装配点）、`bridge/agent_bridge.py` / `agent/protocol/agent.py`、`channel/web/web_channel.py`（Agents API）、`channel/web/static/js/console.js`、Desktop `AgentsPage.tsx`、scheduler 集成、`scenes` 激活/读取。
- **跨 change 依赖**：场景绑定与继承依赖 `port-scene-applications`（或已合入的 `scene-activation`）可用；角色级智能体资源授权对接 `role-resource-authorization`（本 change Phase 1 仅保留可见性字段与过滤接入点，不强制启用完整 visibility UI）。执行隔离/多租户任意代码门槛仍遵循企业化既有约束。
- **接口兼容**：保留 `/api/agents` 既有字段与 `revision` 语义，新增字段向后兼容（缺省行为与今日一致：无 allowlist 则不额外裁剪工具；`skills == null` 仍表示全部）。
- **交付性质**：本步骤生成 OpenSpec change 产物；实现按 `tasks.md` 分阶段，产物完成不等于实现完成。
