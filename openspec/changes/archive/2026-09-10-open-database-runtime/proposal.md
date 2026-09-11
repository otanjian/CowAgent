## Why

`identity_mode=database` 下身份与资源授权已接入，但运行消费者（Web 对话、附件/文件、外部 IM 通道、调度、OpenAI 兼容 API、Agent Bridge）仍按 task 3.11 整体关闭，工作台显示「当前版本尚未开放对话」。企业部署需要开放与 legacy 相当的运行能力，且外部 IM 必须把消息映射到本地账号后才可执行。`tenant-resource-isolation` 规范明确「开放模型/工具执行、外部凭据动作或任意代码必须先验证 10B 硬配额、08S 凭据、09G 单动作审批或执行隔离切片」，因此本 change 一并落地这些前置切片，再全面开放运行面。

## What Changes

- **BREAKING（database 模式）**：拆除运行消费者关闭门——工作台 `can_chat` 不再固定 `runtime_not_enabled`；Bridge 完整初始化；启动通道不再滤成仅 `web`；聊天/文件/上传/语音/调度/OpenAI 等入口去掉 `_guard_not_database` / HTTP `closed` 短路；`_consumer_availability` 与页面能力投影改为按真实开放状态报告。
- Web 对话硬拦：`chat.use` + 目标 Agent 的 `agent.use` 资源 grant + 所选模型的 `model.use`；平台管理员按系统 all、租户管理员按既有旁路处理。缺权返回 403 稳定码；卡片 `unavailable_reason` 区分权限不足，不静默换默认 Agent。
- 新增 `external_identities`（`provider + issuer/corp_id + subject` → `User`）及管理 API（列/绑/解绑，仅管理员）。IM 入站：解析外部身份 → 查绑定 → 校验用户/成员有效 → 用该用户权限并集跑 Agent；未绑定/停用/无成员/无执行权则不调模型，回固定提示并记审计。
- 调度任务创建快照 `user_id`/`tenant_id`/资源，**每次触发前重验**授权；撤权后不得继续执行。
- **执行隔离**：多租户下任意代码（技能脚本、bash 等工具）SHALL 在隔离边界内执行，禁止跨租户读取与越权写入；隔离未验收前相关工具默认拒绝。
- **凭据（08S）**：外部系统凭据 SHALL 加密存储、按租户/资源授权、按需注入、撤权后失效，不落明文。
- **单动作审批（09G）**：外部有副作用动作 SHALL 在执行前经人工审批；未审批不执行。
- **硬配额（10B）**：租户/用户对 token、工具调用、并发、存储等 SHALL 有硬上限，超限拒绝。
- **内部审计（10A）**：敏感操作 SHALL 记录可查询、防篡改的审计事件。
- 更新 `agent-chat-launch` 等规范中「database 不可运行」场景；修订交付文档「线上运行延期」表述。
- **不做**：IM 用户自助绑定 / SSO 扫码建号；默认给内置 `member` 补 `agent.use`/`chat.use`；`database_runtime_enabled` 配置开关；切 legacy 读已迁移库；Desktop 企业登录。

## Capabilities

### New Capabilities

- `database-runtime-consumers`: database 模式下 Web 对话、文件/上传、调度、OpenAI 兼容 API、MCP 预热、Agent Bridge 与外部通道启动的开放边界、关闭门拆除与能力投影。
- `external-identity-binding`: 外部 IM 身份与本地 User 的管理员预绑定、入站解析、未绑定拒绝与审计。
- `execution-isolation`: 多租户任意代码执行的隔离边界、越界拒绝与未验收默认禁用。
- `credential-management`: 外部凭据的加密存储、租户/资源授权、按需注入与撤权失效。
- `action-approval`: 外部有副作用动作的单动作审批、未审批拒绝与审计。
- `resource-quota`: 租户/用户硬配额的计量、强制执行与超限拒绝。
- `audit-log`: 敏感操作的防篡改审计记录与查询边界。

### Modified Capabilities

- `agent-chat-launch`: 将「database 运行消费者关闭」改为「有权可启动、无权拒绝」；保留目标失效与会话归属约束。
- `agent-workbench`: 工作台投影 `can_chat`/`unavailable_reason` 与 database 运行开放对齐。
- `resource-execution-authorization`: 明确对话/模型/代码执行在 database 开放后的调用点重验要求（含 IM 映射用户与隔离前置）。
- `business-permission-catalog`: 确认 `agent.use`/`chat.use`/`model.use` 为对话运行门槛；内置 member 默认仍不自动授予。
- `console-navigation-availability`: 对话相关页/入口不再因 `consumer_closed`/`deferred` 整体不可达（仍按权限投影）。
- `tenant-resource-isolation`: 将「首期消费者关闭」改为「切片验收后开放」，明确开放门槛与隔离边界。

## Impact

- 需求依据：`docs/design/role-resource-authorization-delivery.md` §6.2 未交付「线上运行开放」；`docs/design/user-role-permission-gap-and-plan.md` §5.1～5.2（逐消费者开放与 `external_identities`）；`tenant-resource-isolation` 规范的切片前置。对应 PRD v1.2 原文本地缺失（08S/09G/10B/10A），本 change 依据 `openspec/config.yaml` context 摘要定义行为并在 spec 记录缺口，PRD 恢复后核对，不虚构编号细节。
- 数据唯一归属：`identity.db` 保有用户/租户/成员/角色/grants 与新增 `external_identities`、凭据/审批/配额/审计表（或专用存储）；Agent 工作区与业务会话仍在各 Agent workspace；`config.json`/`team.json` 保有通道配置与实例绑定。不把 IM subject 写成 User 主键。
- 代码范围：`app.py`、`bridge/agent_bridge.py`、`channel/web/web_channel.py`、`auth/{store,service,http_policy,policy}.py`、各 IM channel 入站路径、调度集成、`agent/permission/policy.py` 与工具执行入口、前端 `console.js`、相关 tests 与交付文档。
- 跨 change 依赖：复用已归档的身份/RBAC/资源授权/工作台拆分（`complete-enterprise-identity-access-control`、`add-role-resource-authorization`、`split-agent-configuration-and-workbench` 等）。10A/08S/09G/10B 与执行隔离为本 change 内的独立切片阶段，各自验收。
- 兼容与恢复：legacy 行为不变；database 迁移版本化；回退需相容构建或库快照；启动守卫继续拒绝 legacy 读已迁移库。
