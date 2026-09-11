# 规划产物校验

日期：2026-09-09。本变更已实现并完成阶段 0～5 实施，规划产物与实现产物齐备。

- proposal、design、specs、tasks 四类规划产物齐备，审核取舍记录于 review.md。
- 共 5 份规范：4 项新能力（scene-application-console、scene-activation、scene-workbenches、scene-skills）、1 项既有能力增量（console-information-architecture）；共 13 条新增需求、1 条修改需求、38 个 WHEN/THEN 场景。
- 任务编号唯一，按阶段 0～5 分组，实施完成后全部勾选。
- `openspec validate port-scene-applications --strict` 通过；`openspec list --json` 报告状态 in-progress（归档前）。
- 本地结构核对通过：全部需求含 SHALL/MUST，全部场景标题含 WHEN/THEN；无重复任务编号或误勾选项。
- 场景代码采用独立目录结构：顶层 `scenes/`（`scenes_config.json`、`skill_mapping.json`、`config.py`、`service.py`、`api.py`、`api_workbench.py`、`renderer.py`、`skills/`、`workbenches/`），前端对应 `channel/web/static/js/scenes/`；每个场景在工作台目录单独成子目录，共享底座与各场景内容分离。
- 场景配置 `scenes/scenes_config.json` 的 `required_permission` 与 `scenes/config.py` 的 `can_access_scene` 仅保留接入点，v1 默认全员可见，不启用 RBAC。
- 场景数据表命名统一 `cj-{场景英文名}-{具体表名}`（跨场景 `cj-common-*`），v1 核心路径不建表、仅固定约定。
- 技能经 `skill_mapping.json` 映射表解析（21 个 `skill_name` 语义键 → 实际技能目录），未映射者标注不强行搬运。
- 场景/技能来源 OneAgent 仅作参考，未复制运行时配置、凭据、客户数据或整文件覆盖。
- 本变更不改变既有业务 session、ExecutionRun、scheduler、Channel 协议与消息渠道；不新增持久化字段或数据迁移（表命名规范仅为后续预留约定，v1 不建表）。
- 实现测试：后端场景+策略测试 73 passed；前端场景+侧栏测试 57 passed。另有 `tests/test_session_history_search.py` 2 项为既有失败（`is_platform_admin` 相关，与本变更无关）。

## 已实施产物

- `scenes/`（`__init__`、`scenes_config.json`、`skill_mapping.json`、`config.py`、`service.py`、`renderer.py`、`api.py`、`api_workbench.py`、`workbenches/base.py`、`skills/*`）
- `channel/web/static/js/scenes/`（`index.js`、`registry.js`、`workbenches/*.js`）
- `channel/web/chat.html`（侧栏 `data-view="scenes"`、`#view-scenes` 视图容器含 `#scene-catalog`/`#scene-workbench`、`assets/js/scenes/index.js` 引入）
- `channel/web/static/js/console.js`（`VIEW_META.scenes`、`navigateTo('scenes')` 调 `loadScenesView`、`scenarios`→`scenes` 重定向、`/场景`/`/scenes` 选择器、三语 `scenes_*` 文案）
- `bridge/agent_bridge.py`（`_apply_scene_context`）、`agent/skills/manager.py`（`scenes_dir`）
- `docs/design/scene-application-retrofit-plan.md`（范围、目录结构、表命名、降级策略、RBAC 接入点）
- 测试：`tests/test_scenes_api.py`、`tests/test_scene_activation.py`、`tests/test_scene_skills.py`、`tests/test_scene_workbench.py`、`tests/test_scenes_frontend.cjs`、`tests/test_scene_workbench_frontend.cjs`

> 说明：应用测试、真实接口、组件集成仍需浏览器 Acceptance 与线上运行验证；静态校验与单元测试通过不等于线上 Web 全链路可用。
