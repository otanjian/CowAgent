## Why

OneAgent 已实现一套成熟的「场景应用」体系：`scenes_config.json` 定义 10 个业务分类、26 个场景，每个场景带独立 `system_prompt`、关联技能、`has_workbench` 专业工作台与 `sub_scenes` 子场景，并在对话中激活场景后注入对应提示词。用户希望把这套场景应用回流到本仓库（CowAgent），在侧栏红框处新增「场景应用」菜单。

CowAgent 当前没有场景概念：无 `scenes_config.json`、无场景后端接口、无场景中心 UI、无 `agent/employee` 模块，也没有工程类专业工作台。侧栏「工作台」分组止于「知识库」，用户截图红框即为新增场景应用菜单的落点。

按 `openspec/config.yaml` 约束，OneAgent 仅作参考，不复制运行时配置、凭据、客户数据或整文件覆盖；本变更是对场景体系的**适配回流**，而非直接拷贝。

## What Changes

- 在侧栏红框处（工作台分组「知识库」之后、「管理控制台」之前）新增「场景应用」入口，进入场景中心视图。
- 新增顶层独立 `scenes/` 模块：`scenes_config.json`（适配版，保留分类/场景/子场景/系统提示词/关联技能/工作台元数据及 ERP/导入配置元数据，**剔除凭据、连接串与客户数据**，`required_permission` 字段保留但 v1 默认全员可见）、`config.py`、`service.py`、`api.py`、`renderer.py`、`skills/`、`workbenches/`（每场景一目录）。
- 新增后端场景接口：`GET /api/scenes`（按可见性返回分类与场景）、`POST /api/scenes/activate`（为会话写入场景上下文并重建该会话 Agent 以注入场景提示词），路由指向 `scenes.api`。
- 场景提示词通过已有 `Agent.extra_system_suffix` 注入到会话完整系统提示词末尾，关联技能通过 `skill_manager` 选择集进入 `_build_skills_section`。
- 前端新增独立目录 `channel/web/static/js/scenes/`（`index.js`、`registry.js`、`workbenches/`）：场景中心（分类页签 + 场景卡片 + 空态 + 场景详情）、场景选择器（`/场景` 命令）与激活 greeting。
- 前端专业工作台：通用工作台（`base` + 子场景面板 + 文件导入/ERP 元数据）及约 8 类专业工作台（质量追溯、SAP 数据分析、生产排产、凭证辅助、财税专家、会计准则、生产计划、财务报表审查），按场景类型分发。
- 搬运场景关联技能到 `scenes/skills/`：先建 `skill_mapping.json` 映射表（21 个 `skill_name` 语义键 → 实际技能目录，逐一核对），再按映射表搬运，未映射者标注「未映射」不强行搬运；由 CowAgent SkillManager 纳入可发现目录，供场景调用。
- 数据表命名规范：场景应用引入的数据库表 SHALL 采用 `cj-{场景英文名}-{具体表名}`（跨场景 `cj-common-*`），v1 核心路径不建表、仅固定约定。
- RBAC 接入点：`scenes_config.json` 与场景接口保留 `required_permission` / 分类过滤结构，v1 不对登录用户做场景级过滤，对接在建 `add-role-resource-authorization` 的权限模型留作后续阶段（本 change 仅设计接入点，不启用）。

## Capabilities

### New Capabilities

- `scene-applications-console`：场景应用侧栏入口、场景中心视图（分类、卡片、空态、详情）、场景选择器与激活交互。
- `scene-activation`：场景激活接口把场景上下文写入会话，并在 Agent 重建时注入场景 `system_prompt` 与关联技能选择，使新会话以该场景身份对话。
- `scene-workbenches`：通用工作台与专业工作台的子场景面板、文件导入、ERP 元数据交互。
- `scene-skills`：场景关联技能集在 CowAgent SkillManager 中可发现、可按场景选用。

### Modified Capabilities

- `console-information-architecture`（既有）：其「未开放功能不伪装为可用菜单」需求当前把「场景应用」列为移除的占位项；本变更把「场景应用」从占位转为真实入口，需反转该约束（详见 MODIFIED spec `console-information-architecture`）。

## Impact

- 新增顶层 `scenes/` 模块（与 `agent/`、`channel/`、`auth/` 平级）：`scenes_config.json`、`skill_mapping.json`、`config.py`、`service.py`、`api.py`、`renderer.py`、`skills/`（按映射表搬入）、`workbenches/`（每场景一目录）。
- 接入文件：`channel/web/chat.html`（侧栏项、`view-scenes` 容器、引入 `scenes/index.js`）、`channel/web/static/js/console.js`（VIEW_META 增 `scenes` 项、`navigateTo` 调用 `loadScenesView`）、`agent/protocol/agent.py`（`extra_system_suffix` 场景注入）、`agent/skills/manager.py`（纳入 `scenes/skills` 可发现目录）、`channel/web/web_channel.py`（`/api/scenes`、`/api/scenes/activate` 路由指向 `scenes.api`）。
- 新增前端目录：`channel/web/static/js/scenes/`（`index.js`、`registry.js`、`workbenches/`）。
- 数据唯一归属保持：场景与技能来源 OneAgent 仅作参考，`scenes/scenes_config.json` 为 CowAgent 自身配置；场景不在租户/身份域持久化，激活上下文为会话级内存状态。
- 不复制 OneAgent 的 `agent/employee`、`auth/rbac.py` 专属实现；`scenes/config.py` 的 `can_access_scene` 仅保留接入点，CMV 首期不启用场景级 RBAC。
- 不改变既有业务 session、ExecutionRun、scheduler、Channel 协议与消息渠道；不新增持久化字段或数据迁移（场景数据表命名规范仅为后续持久化预留约定，v1 核心路径不建表）。
- 测试兼容：新增 `tests/test_scenes_api.py`（接口）、`tests/test_scene_activation.py`（注入）、前端 `tests/test_scenes_frontend.cjs`（侧栏与场景中心），并核对既有侧栏/账户前端测试不因新菜单项断言过时。

## Open Questions

- 场景关联技能中依赖外部网络/私有系统的（如 SAP、钉钉会议、天机商查、海康录像）在 CowAgent 环境下如何降级：v1 搬运 SKILL 文案能力，靠 `skill_name` 关联，实际网络依赖由运行环境决定，不复制 OneAgent 的凭据与连接配置。
- 专业工作台依赖的 OneAgent `agent/employee` 服务与特定数据源（ERP 元数据）在本仓库无对应模块，v1 以适配版元数据交互与前端面板呈现为主，端到端数据接通需后续阶段。
