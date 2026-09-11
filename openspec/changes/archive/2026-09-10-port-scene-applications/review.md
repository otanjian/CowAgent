# 审核取舍记录

日期：2026-09-09。本变更对 OneAgent 场景应用回流做适配，不整文件覆盖。

## 范围取舍

- 用户选定「完整回流」：全部 26 场景、21 个 `skill_name` 语义键（经映射表关联技能目录）、通用 + 专业工作台全量搬运；不在本变更内拆分阶段交付。
- 用户选定「v1 对所有登录用户开放场景」：RBAC 仅保留接入点字段与过滤函数占位，不启用场景级权限过滤，对接在建 `add-role-resource-authorization` 留作后续。
- 场景「知识库」之后新增菜单项（红框落点）；纳入既有 `VIEW_META`/`navigateTo`，旧 `scenarios` 占位由真实 `scenes` 视图承接。
- 场景数据表命名统一 `cj-{场景英文名}-{具体表名}`（跨场景 `cj-common-*`），v1 核心路径不建表、仅固定约定。

## 设计权衡

- 场景提示词注入走既有 `Agent.extra_system_suffix`（`agent/protocol/agent.py:201`），不新造注入通道，保持提示词段顺序（工具→技能→记忆→知识→工作空间→场景后缀）。
- 场景代码独立成顶层 `scenes/` 模块（与 `agent/`、`channel/`、`auth/` 平级），共享底座（`config`/`service`/`api`/`renderer`）+ 各场景内容分离（`workbenches/<scene>`、`skills/<skill>`）；前端对应 `channel/web/static/js/scenes/`，避免与既有业务代码耦合，也避免继续膨胀 `console.js`。
- 会话级场景上下文用 `WebChannel._class_scenes[session_id]` 类属性承载（与 OneAgent 同款），单测隔离清理，避免跨测试泄漏。
- 专业工作台拆独立目录按场景分组，统一 `openSceneById`（前端）分发；场景技能先建 `skill_mapping.json` 映射表解析 `skill_name`，再由既有 `SkillManager.loader.load_all_skills()` 自动发现并纳入 `scenes/skills`，`selection` 按场景裁剪，不新增注册表。
- 数据表命名规范先行固定 `cj-{场景英文名}-{具体表名}`，共享底座不建表，后续场景/工作台持久化直接复用。

## 风险与降级

- OneAgent `agent/employee` 服务与 ERP/SAP 数据源在 CowAgent 无对应模块，v1 专业工作台以适配版元数据交互与前端面板呈现为主，端到端数据接通留待后续变更。
- 外部依赖技能（SAP、钉钉、天机、海康）在 CowAgent 环境可能不可用，属预期降级，仅承载 SKILL 文案与本地能力，不复制运行配置与凭据。
- 场景与技能搬运需先建 `skill_mapping.json` 映射表核对 `skill_name` 语义键与技能目录的对应，避免场景-技能关联失效；无法确定者标注「未映射」。
