## Why

产品规划 3.1 已明确成员需要独立完成个人助手的配置与开发，但现有控制台、所有权授权和渠道身份绑定规则尚不能组成完整的成员自助流程。需要将已确认的个人控制台职责落实为可实施、可验收的规格，同时修正旧规范中管理员可读取成员私有资产的例外。

## What Changes

- 为内置 `member` 提供个人智能体、个人渠道、个人记忆以及已授权工具与技能的控制台入口；页面、对象动作及运行状态分别投影。
- 所有权授权从私有智能体的读、用扩展到本人配置、调试和启停；支持成员自行创建及删除自建私有智能体，系统自动供应的专属助理继续单独管理。
- 支持个人记忆查看、编辑、删除与清空，区分用户个人记忆、私有智能体记忆和租户共享记忆，保证索引与后续检索一致。
- 新增个人渠道作用域，成员在租户策略与配额内自行维护凭证、接入和绑定；复用已有渠道运行链路，不向成员开放平台或租户公共渠道的维护接口。
- **BREAKING**：外部身份由“禁止成员自助绑定”调整为“管理员仍可预绑定，成员可经本人控制权验证自助绑定”；成员不能指定任意外部身份直接建绑。
- **BREAKING**：管理员身份及平台 `all` 不再自动授予成员私有智能体资产、个人记忆、凭证和私有会话的内容访问权限；保留明确的资源归属、用量和渠道停用治理能力。
- 公共渠道的公共路由只绑定公共智能体；访问个人智能体须使用已验证的本人身份及私聊个人路由，不将个人记忆注入群聊。
- 保留公共工具与技能的既有显式维护授权，成员的默认个人能力只提供获权目录、选用和个人参数配置，不自动获得公共安装、编辑、全局启停或授权能力。

## Capabilities

### New Capabilities

- `member-personal-console`：个人入口、默认菜单、对象动作投影、租户切换和分阶段启用。
- `user-private-agent-management`：成员自建私有智能体及本人维护、调试、删除的完整生命周期。
- `personal-channel-configuration`：个人渠道所有权、凭证与版本、配额、启停及个人消息路由。

### Modified Capabilities

- `rbac-authorization`：本人私有资源动作的统一所有权授权，移除管理员私有内容旁路。
- `console-navigation-availability`：登记个人页面并迁移内置角色的安全菜单授权，保持显式菜单与实际能力一致。
- `user-personal-agent-provisioning`：区别系统专属助理和自建私有智能体，修正供应幂等条件及私有归属规则。
- `user-personal-context`：个人记忆管理、租户与用户双重边界、并发修改和清空后的检索一致性。
- `tenant-channel-configuration`：公共渠道只使用公共路由目标，明确个人作用域与公共配置隔离。
- `external-identity-binding`：受验证的本人自助绑定、作用域内解绑及个人入站尝试的隐私保护。
- `tenant-skills-tools-console`：已授权目录与个人使用配置，隔离公共维护权限。
- `tenant-resource-isolation`：私有资产所有者优先，文件实际归属和新增自助入口的启用边界。
- `platform-file-browsing`：平台文件根、工作区和私有预览均不能绕过本人私有内容边界。
- `tenant-knowledge-console`：私有知识资产读取与写入前的 owner 检查，衔接在途知识写权限变更。
- `credential-management`：个人凭证的明确所有者及按本人用途使用，不扩大资源执行授权。

## Impact

现有代码接入点包括 `auth/policy.py`、`auth/service.py`、`auth/store.py`、`auth/credential.py`、`agent/personal_assistant.py`、`agent/memory/manager.py`、`channel/external_identity.py`，以及 Web 路由、能力摘要、管理 handler、控制台和相关 i18n。新增个人 API 需要登记方法级策略；现有管理接口继续保留各自权限边界。

数据唯一归属保持在身份域：智能体复用 `agent_bindings.private_owner_user_id`；个人渠道扩展现有渠道实例并绑定唯一租户和成员；外部身份继续复用全局唯一三元组映射，另以当前租户内的个人路由关系表达接入，不复制账号；凭证使用既有密文版本存储；个人记忆仍存于可信租户与用户作用域。

跨 change 依赖与冲突：

- `fix-private-agent-owner-reachability`：复用统一 owner 读/用授权入口，本 change 明确扩展写与启停，替代其“所有权不放宽写动作”的旧限制。
- `fix-private-agent-file-scope`、`open-tenant-workspace-console`：复用实际文件来源与工作区访问链路；前者仍含管理员私有读取例外，本 change 负责收紧，文件隔离的真实证据是私有维护开放前置。
- `restrict-knowledge-write-authorization`：保留其公共知识写权限及权限目录迁移方案；对私有数据增加优先 owner 检查，修正其管理员可写他人私有 own 库的冲突分支。该 change 尚在进行中，不能按已完成能力依赖。
- `personalize-personal-assistant-owner-fields`、`add-tenant-default-agent-selection`：保留本人资料个人化及默认入口优先序，不让自建智能体覆盖专属助理登记。
- 凭据、审计、硬配额、执行授权及适用的审批/隔离能力按实际接入切片验收；任务状态与规范文件存在不等同于生产启用证据。

本 change 交付提案、设计、增量规格与待执行任务；现有实现状态以实施阶段的实际证据为准。

