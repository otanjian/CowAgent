## Context

本变更把 OneAgent 的场景应用体系回流到 CowAgent。按 `openspec/config.yaml` 约束，OneAgent 仅作参考，不复制运行时配置、凭据、客户数据或整文件覆盖。实施前已盘点两个仓库：

**OneAgent（源头）关键实现**
- `scenes_config.json`（212KB，10 分类 / 26 场景）定义场景 `system_prompt`、`skill_name`、`has_workbench`、`sub_scenes`、`erp_config`、`import_config`、`required_permission`。
- `channel/web/web_channel.py`：`ScenesHandler`(`GET /api/scenes`) 与 `SceneActivateHandler`(`POST /api/scenes/activate`)；激活时把 scene 写入 `WebChannel._class_scenes[session_id]`，删除该 session 的 Agent 实例以重建带场景 prompt 的 Agent。
- `auth/scene_access.py`：按 `required_permission` / `scenes.use.<category>` 过滤场景。
- 前端 `static/js/console.js`：场景中心（分类页签+卡片+空态）、场景选择器、~12 个专业工作台渲染器与子场景面板。

**CowAgent（目标）相应接缝**
- 侧栏在 `chat.html` 工作台分组，红框即「知识库」之后、「管理控制台」之前。
- 视图注册在 `static/js/console.js` 的 `VIEW_META` + `navigateTo()`；有 `UNAVAILABLE_VIEWS` 处理「尚未开放」目标。
- `agent/protocol/agent.py:201` `Agent.get_full_system_prompt()` 通过 `self.extra_system_suffix` 把额外指令追加到重建后的完整系统提示词末尾 —— 场景提示词注入点。
- `agent/skills/manager.py` 的 `selection`（可选技能集）进入 `_build_skills_section` —— 场景技能选择点。
- `channel/web/web_channel.py` 的 `_WEB_URLS` 登记路由，`_require_auth()` 鉴权。

## Goals / Non-Goals

**Goals：** 侧栏新增「场景应用」；场景中心可浏览分类与场景；场景可激活并注入提示词与关联技能；通用及专业工作台可交互；场景技能可被发现；全量场景与技能回流；RBAC 字段保留、v1 默认全员可见。

**Non-Goals：** 不复制 OneAgent 的 `agent/employee`、`auth/rbac.py` 专属实现；不在本变更启用场景级 RBAC（仅设计接入点）；不复制 ERP/SAP/钉钉/海康等外部运行凭据与客户数据；不改既有业务 session、ExecutionRun、scheduler、Channel 协议与消息渠道；不做数据迁移或新增持久化字段（表命名规范仅为后续预留，v1 不建表）。

## Decisions

### 1. 适配版场景配置

在仓库根目录新增 `scenes_config.json`，结构沿用 `{ categories: [{id,name,icon,color}], scenes: [{id,name,category,description,icon,skill_name,system_prompt,has_workbench,workbench_title,sub_scenes:[...],required_permission}] }`。移植时清除 OneAgent 配置中的连接串、凭据与客户数据，仅保留场景/子场景/提示词/技能/工作台/导入与 ERP 元数据。`required_permission` 字段保留在每条场景上，v1 不据此过滤。

### 2. 后端场景接口

在 `channel/web/web_channel.py` 新增两个 handler，并在 `_WEB_URLS` 登记：
- `GET /api/scenes` → `ScenesHandler.GET()`：读配置，做可选取景过滤（v1 默认全部），返回 `{status, categories:[], scenes:[], has_scene_access}`；配置缺失/解析失败返回空结构。
- `POST /api/scenes/activate` → `SceneActivateHandler.POST()`：查场景（含在子场景中查找并合并父元数据），写会话上下文，删除该会话 Agent 实例以触发重建。复用 `_require_auth()`。

会话级场景上下文存储：oneagent 用 `WebChannel._class_scenes[session_id]`（类属性共享状态）。为与 CowAgent 既有会话管理对齐，本变更在 Agent 实例重建路径上通过 `extra_system_suffix` 注入，会话上下文以 `WebChannel._class_scenes[session_id]` 同款类属性承载（隔离于单测、可清理）。

### 3. 场景提示词注入

`Agent.get_full_system_prompt()` 已支持 `extra_system_suffix`。场景激活后，Agent 重建时把 `scene.system_prompt` 写入 `extra_system_suffix` 并推进到提示词末尾。未激活场景的会话不设置该后缀，行为与现有一致。注入不改变既有提示词段顺序（工具→技能→记忆→知识→工作空间→场景后缀）。

### 4. 场景技能选择

CowAgent `SkillManager` 已有 `selection`（可选技能集）。场景激活时按 `skill_name` 构造选择集，交给该会话 Agent 的 `skill_manager`；未配置或未安装时不注入，保持可用技能不受影响。多场景共享技能复用同一实例。

### 5. 顶层独立目录结构

场景应用代码高度独立，顶层建专用目录 `scenes/`（与 `agent/`、`channel/`、`auth/` 平级），共享底座与各场景内容分离：

```
scenes/
├── __init__.py
├── scenes_config.json            # 适配版配置（10 分类 / 26 场景 + required_permission）
├── skill_mapping.json            # skill_name → 技能目录映射表（逐一核对）
├── config.py                     # 配置路径解析、加载/校验、可访问性占位(can_access_scene)
├── service.py                    # 场景数据服务：get_catalog / activate / 会话上下文读写
├── renderer.py                   # 工作台渲染逻辑分发（openSceneById 的后端对应物，可选）
├── api.py                        # 场景 HTTP 处理器（ScenesHandler / SceneActivateHandler）
├── skills/                       # 场景关联技能（按映射表搬入）
│   ├── procurement-supplier-risk/
│   ├── taxation-expert/
│   └── ...
└── workbenches/                  # 工作台专用组件（每场景一个子目录）
    ├── base.py                   # 通用工作台基类（子场景面板/文件导入/ERP 元数据）
    ├── voucher/                  # 凭证辅助
    ├── tax/                      # 财税专家 / 会计准则
    ├── financial_audit/          # 财务报表审查
    ├── sap_analysis/             # SAP 数据分析
    ├── quality_trace/            # 质量追溯
    ├── scheduling/               # 生产计划 / 生产排产
    └── ...
```

前端对应独立目录 `channel/web/static/js/scenes/`：

```
channel/web/static/js/scenes/
├── index.js        # 场景中心：loadScenesView/renderScenesView/选择器/activateScene/greeting
├── registry.js     # openSceneById 分发映射
└── workbenches/    # 各工作台渲染器
    ├── base.js
    ├── voucher.js
    ├── tax.js
    ├── financial_audit.js
    ├── sap_analysis.js
    ├── quality_trace.js
    ├── scheduling.js
    └── ...
```

`chat.html` 仅引入 `scenes/index.js`（内部按需加载 workbenches），`console.js` 仅在 `VIEW_META` 增加 `scenes` 项并在 `navigateTo` 调用 `loadScenesView()`，场景逻辑不塞进 `console.js`。既有 `UNAVAILABLE_VIEWS` 中的 `scenarios` 占位（旧场景应用占位）由真实 `scenes` 视图承接，`backup`/`open_api` 仍为未开放占位。

### 6. 后端接口与场景上下文

- `channel/web/web_channel.py` 的 `_WEB_URLS` 登记 `/api/scenes` 与 `/api/scenes/activate` 指向 `scenes.api` 的处理器；处理器复用既有 `_require_auth()`。
- `GET /api/scenes`：由 `scenes.service.get_catalog()` 返回分类/场景，缺配置返回空结构。
- `POST /api/scenes/activate`：由 `scenes.service.activate()` 查场景（含子场景合并父元数据）、写会话上下文、失效该会话 Agent。
- 会话级场景上下文用 `WebChannel._class_scenes[session_id]` 承载（隔离于单测、可清理）。

### 7. 场景提示词注入与技能选择

- 场景激活后，`Agent.get_full_system_prompt()` 通过 `extra_system_suffix` 追加 `scene.system_prompt`，提示词段顺序保持 工具→技能→记忆→知识→工作空间→场景后缀。
- 场景关联技能按 `skill_name` 构造 `SkillManager.selection` 选择集，交给该会话 Agent；未配置或未安装时不注入。

### 8. 专业工作台分批分流

OneAgent 约 8 类专业工作台（质量追溯、SAP 数据分析、生产排产、凭证辅助、财税专家、会计准则、生产计划、财务报表审查）+ 通用工作台，本变更在 `scenes/workbenches/` 与 `channel/web/static/js/scenes/workbenches/` 各自成目录。分发经 `openSceneById`（前端）与 `renderer.py`（后端，如需要）统一：凭证→voucher、财税/会计准则→tax、财务审计→financial_audit、SAP 分析→sap_analysis、质量追溯→quality_trace、生产计划/排产→scheduling、其余→通用 base。

### 9. 技能搬运与映射表

OneAgent 场景配置里的 `skill_name`（21 个去重值）是**场景级语义键**，绝大多数并非技能目录名——交叉核对后，仅 `pmc-scheduler-hmt-qd`、`quality-trace`、`sap-integration` 3 个与 `skills/` 目录同名；其余（如 `finance-expert`、`procurement-supplier`、`sales-customer`）需映射到实际目录（如 `taxation-expert`、`procurement-supplier-risk`、`sales-quotation`）。

因此本变更 SHALL 先建立 `scenes/skill_mapping.json` 映射表：`{ <skill_name>: { "skill_dir": "<skills/ 下目录>", "name": "<SKILL.md name>", "note": "<语义说明>" } }`，逐一核对 21 个 `skill_name` 与 41 个技能目录的对应关系，再按映射表搬运命中的技能目录到 `scenes/skills/`。未在映射表中命中或无法确定对应目录的 `skill_name`，v1 标注为「未映射」，不强行搬运、不阻断场景。技能注册由既有 `SkillManager.loader.load_all_skills()` 自动发现（纳入 `scenes/skills` 目录），`selection` 按场景裁剪。

### 10. RBAC 接入点（本变更不启用）

`scenes_config.json` 保留 `required_permission`；`scenes.config.py` 的 `can_access_scene(scene, permissions)` 占位默认返回真。后续 `add-role-resource-authorization` 落地后再注入实际权限判断，本变更不改变权限、租户、身份域。

### 11. 场景数据表命名规范

场景应用引入的数据库表 SHALL 采用统一命名格式 `cj-{场景英文名}-{具体表名}`（`cj` 为「场景」固定前缀，场景英文名取自 `scenes_config.json` 场景 `id`，具体表名用下划线小写）。例如采购供应商场景为 `cj-procurement-supplier`，财务凭证为 `cj-finance-voucher`。跨场景共享的表 SHALL 用 `cj-common-{具体表名}`。共享底座（`config`/`service`/`api`）不引入表；仅在某场景/工作台确需本地持久化时按此规范建表。v1 核心路径不强制建表，命名规范先行固定以便后续阶段直接复用。

## Risks / Trade-offs

- 场景/技能来自 OneAgent，`skill_name`（21 个语义键）与技能目录（41 个）非一一对应，须先建映射表逐一核对；无法确定映射的技能键标注「未映射」，不强行搬运。外部依赖技能在 CowAgent 环境可能不可用，属预期降级。
- `scenes/` 为新增顶层模块，需与既有 SkillManager 目录合并逻辑兼容（`scenes/skills` 纳入可发现目录）；专业工作台依赖的 `agent/employee` 服务与 ERP 数据源在 CowAgent 无对应模块，v1 以适配版元数据交互与前端面板呈现为主，端到端数据接通留待后续阶段。
- 会话级场景上下文用类属性承载，需在单测中隔离并清理，避免跨测试泄漏。
- 场景技能约 40 个目录、21 个 `skill_name` 语义键，须先建映射表再搬运，工作量集中在前置核对；需按 SKILL.md 逐个核对结构与外部依赖。

## Open Questions

- 场景关联技能中依赖外部网络/私有系统（SAP、钉钉会议、天机商查、海康）在 CowAgent 的降级策略：v1 承载 SKILL 文案，靠 `skill_name` 关联，实际网络访问由运行环境决定。
- `scenes/workbenches/` 各工作台是否引入后端渲染（`renderer.py`）还是纯前端面板，视各场景复杂度与数据源而定。
- 专业工作台端到端数据接通（ERP 元数据、导入处理到 Agent 读取）是否需要独立后续变更。
