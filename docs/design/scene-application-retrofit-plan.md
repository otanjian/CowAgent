# 场景应用回流实现方案

日期：2026-09-09。基线：CowAgent 当前工作区（`port-scene-applications` 变更，含未提交内容）和本地 `../oneagent` 源码。本文是代码核对与实现说明，不代表部署验收或端到端 ERP/SAP 数据接通。承接用户要求：把 OneAgent 场景应用体系回流到 CowAgent，侧栏新增「场景应用」菜单，且场景应用代码相对独立、有专门目录、每场景代码单独文件夹。

## 1. 目标与范围

把 OneAgent 的「场景应用」体系整体回流到 CowAgent：

- 侧栏「工作台」分组新增「场景应用」入口（位于「知识库」之后、「管理控制台」之前）。
- 场景中心可浏览分类与场景卡片；场景可激活并注入提示词与关联技能。
- 通用及专业工作台可交互（子场景面板、功能模块卡片、文件导入、ERP 元数据展示）。
- 场景关联技能可被 `SkillManager` 发现。
- 全量场景（10 分类 / 26 场景）与技能（约 40 个目录、21 个 `skill_name` 语义键）回流。
- RBAC 字段保留、v1 默认全员可见（仅设计接入点，不在本变更启用）。

## 2. 独立目录结构

场景应用代码放置于顶层独立目录 `scenes/`（与 `agent/`、`channel/`、`auth/` 平级），共享底座与各场景内容分离：

```text
scenes/
├── __init__.py                    # 模块文档与设计约束
├── scenes_config.json             # 适配版配置（10 分类 / 26 场景 + required_permission）
├── skill_mapping.json             # skill_name → 技能目录映射表（逐一核对 21 个语义键）
├── config.py                      # 配置路径解析、加载/校验、可访问性占位(can_access_scene)
├── service.py                     # 场景数据服务：get_catalog / activate / 会话上下文读写
├── renderer.py                    # 工作台渲染逻辑分发（skill_name/scene_id → workbench 类型）
├── api.py                         # 场景 HTTP 处理器（ScenesHandler / SceneActivateHandler）
├── api_workbench.py               # 工作台文件导入处理器（SceneWorkbenchImportHandler）
├── skills/                        # 场景关联技能（按 skill_mapping.json 搬入）
│   ├── procurement-supplier-risk/
│   ├── taxation-expert/
│   └── ...
└── workbenches/
    ├── __init__.py
    └── base.py                    # 共享底座：扩展名校验、workbench 元数据负载（剔除凭据）
```

前端对应独立目录 `channel/web/static/js/scenes/`：

```text
channel/web/static/js/scenes/
├── index.js        # 场景中心：loadScenesView / renderScenesView / showScenePicker / activateScene / openSceneById
├── registry.js     # ScenesRegistry：workbench 类型分发映射 + 渲染器注册
└── workbenches/    # 各工作台渲染器
    ├── base.js               # 通用工作台（子场景面板/表单/导入/ERP 元数据）
    ├── voucher.js
    ├── tax.js
    ├── financial_audit.js
    ├── sap_analysis.js
    ├── quality_trace.js
    └── scheduling.js
```

`chat.html` 仅引入 `scenes/index.js`，其内部动态加载 `registry.js` 与各 workbench 渲染器；`console.js` 仅在 `VIEW_META` 增加 `scenes` 项并在 `navigateTo` 调用 `loadScenesView()`，场景逻辑不塞进 `console.js`。既有 `UNAVAILABLE_VIEWS` 中的 `scenarios` 占位由真实 `scenes` 视图承接，`backup`/`open_api` 仍保留为未开放占位。

## 3. 场景激活与提示词注入

- `GET /api/scenes` → `scenes.api.ScenesHandler`：读配置，v1 返回全部场景（保留 `required_permission`），缺配置返回空结构。
- `POST /api/scenes/activate` → `scenes.api.SceneActivateHandler`：查场景（含子场景合并父元数据）、写会话上下文、失效该会话 Agent 实例以触发重建。
- 场景提示词经 `AgentBridge._apply_scene_context` 写入 `extra_system_suffix`，由 `Agent.get_full_system_prompt()` 追加到完整提示词末尾；会话级场景上下文以 `scenes.service._session_scenes` 承载。

## 4. 技能关联

`scenes_config.json` 的 `skill_name` 是场景级语义键，与 `oneagent/skills` 目录并非一一对应（仅 `pmc-scheduler-hmt-qd`、`quality-trace`、`sap-integration` 3 个同名）。因此建立 `scenes/skill_mapping.json` 逐一映射 21 个 `skill_name` 到技能目录；无法确定者标注「未映射」，不强行搬运、不阻断场景。`SkillManager` 把 `scenes/skills` 纳入可发现目录，`selection` 按场景裁剪。

## 5. 工作台与文件导入

- 场景卡片 / `/场景` 选择器按 `ScenesRegistry.resolveWorkbenchType` 分发到对应工作台渲染器（凭证→`voucher`、财税→`tax`、财务审查→`financial_audit`、SAP 分析→`sap_analysis`、质量追溯→`quality_trace`、生产排产→`scheduling`、其余→`base`）。
- 文件导入经 `POST /api/scenes/workbench/import`（`api_workbench.SceneWorkbenchImportHandler`）以 base64/文本提交，按子场景 `import_config.accept` 校验扩展名后保存到工作区临时目录 `<workspace>/tmp/scenes/` 供 Agent 读取。
- ERP 元数据仅作展示与查询入口（`build_workbench_payload` 只保留 `enabled/systems`，剔除连接串/凭据）。

## 6. 场景数据表命名规范

场景应用引入的数据库表统一用 `cj-{场景英文名}-{具体表名}`（`cj` 为「场景」固定前缀），例如采购供应商场景为 `cj-procurement-supplier`、财务凭证为 `cj-finance-voucher`。跨场景共享表用 `cj-common-{具体表名}`。共享底座（`config`/`service`/`api`）不引入表；仅在某场景/工作台确需本地持久化时按此规范建表。v1 核心路径不强制建表，命名规范先行固定。

## 7. RBAC 接入点（本变更不启用）

`scenes_config.json` 保留每条场景的 `required_permission`；`scenes/config.py` 的 `can_access_scene(scene, permissions)` 占位默认返回真。后续 `add-role-resource-authorization` 落地后按场景 `required_permission` 做实际权限判断，本变更不改变权限、租户、身份域。

## 8. 降级策略与约束

- 外部依赖技能（SAP、钉钉会议、天机商查、海康）在 CowAgent 可能不可用，属预期降级：v1 承载 SKILL 文案，靠 `skill_name` 关联，实际网络访问由运行环境决定。
- OneAgent 仅作参考，不复制运行时配置、凭据、客户数据或整文件覆盖；搬运技能时已剔除 webhook URL 等敏感信息。
- 会话级场景上下文以类属性承载，单测中隔离并清理，避免跨测试泄漏。
- 端到端 ERP/SAP 数据接通（真实查询、写回）留待后续变更，本变更以适配版元数据交互与前端面板呈现为主。

## 9. 实现与测试对照

| 阶段 | 后端 | 前端 | 测试 |
| --- | --- | --- | --- |
| 0 模块骨架 | `scenes/`、`config/service/api/renderer` | `chat.html` 视图容器 | `test_scenes_api.py` |
| 1 提示词注入 | `AgentBridge._apply_scene_context` | — | `test_scene_activation.py` |
| 2 技能 | `skill_mapping.json`、`manager.py` | — | `test_scene_skills.py` |
| 3 场景中心 | `ScenesHandler/SceneActivateHandler` | `scenes/index.js`、`registry.js`、`console.js` | `test_scenes_frontend.cjs` |
| 4 工作台 | `api_workbench.py`、`workbenches/base.py` | `scenes/workbenches/*.js` | `test_scene_workbench.py`、`test_scene_workbench_frontend.cjs` |
