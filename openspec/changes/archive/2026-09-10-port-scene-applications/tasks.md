# 场景应用回流实施任务

> 说明：任务按阶段分组，覆盖实现、测试、迁移与文档。勾选以 `- [x]` 表示完成。RBAC 接入点仅设计与预留，本变更不启用。

## 阶段 0：场景模块目录与配置

- [x] 创建顶层 `scenes/` 模块目录骨架：`__init__.py`、`config.py`、`service.py`、`api.py`、`renderer.py`、`skills/`、`workbenches/`
- [x] 创建适配版 `scenes/scenes_config.json`（10 分类 / 26 场景，剔除凭据/连接串/客户数据，保留 `required_permission`）
- [x] 在 `scenes/config.py` 实现配置路径解析、加载/校验、`can_access_scene` 占位（默认返回真）
- [x] 在 `scenes/service.py` 实现 `get_catalog`（返回分类/场景，缺配置返回空结构）与 `activate`（查场景含子场景、写会话上下文、失效该会话 Agent）
- [x] 在 `scenes/api.py` 实现 `ScenesHandler`（`GET /api/scenes`）与 `SceneActivateHandler`（`POST /api/scenes/activate`）
- [x] 在 `channel/web/web_channel.py` 的 `_WEB_URLS` 登记 `/api/scenes` 与 `/api/scenes/activate` 指向 `scenes.api` 处理器
- [x] 编写 `tests/test_scenes_api.py`：正常返回、配置缺失/解析失败、缺参/场景不存在、子场景激活
- [x] 运行测试确保 `/api/scenes` 与 `/api/scenes/activate` 通过

## 阶段 1：场景提示词注入

- [x] 在 `agent/protocol/agent.py` 场景激活路径把 `scene.system_prompt` 写入 `extra_system_suffix`（`Agent.__init__` 已定义 `extra_system_suffix` 属性，注入由 `AgentBridge._apply_scene_context` 在会话 Agent 获取时写入）
- [x] 确认 `get_full_system_prompt()` 在重建时把 `extra_system_suffix` 追加到末尾（保持工具→技能→记忆→知识→工作空间→场景后缀顺序）
- [x] 会话级场景上下文 `WebChannel._class_scenes[session_id]` 的单测隔离与清理（上下文承载于 `scenes.service._session_scenes`，测试 setUp/tearDown 清理）
- [x] 编写 `tests/test_scene_activation.py`：激活后注入场景提示词、未激活不注入、技能未安装不阻断
- [x] 运行测试确保注入逻辑通过

## 阶段 2：技能映射表与搬运关联

- [x] 建立 `scenes/skill_mapping.json`：逐一核对 21 个去重 `skill_name` 与 OneAgent `skills/` 目录的对应关系（含 SKILL.md `name`），无法确定者标注「未映射」
- [x] 按映射表适配搬入 OneAgent `skills/*` 命中的技能到 `scenes/skills/`，保持 SKILL.md 结构
- [x] 在 `agent/skills/manager.py` 把 `scenes/skills` 纳入 SkillManager 可发现目录
- [x] 确认外部依赖技能（SAP、钉钉、天机、海康等）不复制运行配置与凭据，仅承载 SKILL 文案与本地能力
- [x] 用 `SkillManager.selection` 按映射表解析 `skill_name` 构造技能选择集
- [x] 编写 `tests/test_scene_skills.py`：映射表覆盖全部技能键、未映射键不阻断、技能注册、多场景共享复用、技能目录非法不阻断
- [x] 运行测试确保技能发现与关联通过

## 阶段 3：前端场景中心（独立目录）

- [x] 在 `channel/web/chat.html`「知识库」侧栏项后新增 `data-view="scenes"` 项与三语文案
- [x] 在 `chat.html` 新增 `#view-scenes` 视图容器
- [x] 在 `chat.html` 引入新增 `assets/js/scenes/index.js`（内部按需加载 workbenches）
- [x] 在 `console.js` 的 `VIEW_META` 增加 `scenes` 项，`navigateTo()` 处理 `viewId==='scenes'` 调用 `loadScenesView()`
- [x] 将场景应用从占位转真实入口：`UNAVAILABLE_VIEWS` 中的 `scenarios` 占位改由真实 `scenes` 视图承接（旧 `scenarios` 直链/收藏重定向到 `scenes`，不再走「功能尚未开放」分支）；`backup`/`open_api` 仍保留为未开放占位
- [x] 在 `console.js` 补充 `menu_scenes`/`scenes_title`/`scenes_empty` 等三语文案（zh/en/繁）
- [x] 编写 `channel/web/static/js/scenes/index.js`：`loadScenesView`/`renderScenesView`（分类页签+卡片+空态）、`ensureScenePickerData`/`showScenePicker`（`/场景` 选择器）、`activateScene`/`openSceneById`（分发）、greeting 注入
- [x] 编写 `channel/web/static/js/scenes/registry.js`：`openSceneById` 分发映射
- [x] 编写 `tests/test_scenes_frontend.cjs`：侧栏项存在、场景中心渲染、分类空态、`/场景` 选择器
- [x] 运行前端测试并核对既有 `test_sidebar_account_frontend.cjs` 断言不因新菜单项过时

## 阶段 4：专业工作台（独立目录）

- [x] 编写 `scenes/workbenches/base.py` 与 `channel/web/static/js/scenes/workbenches/base.js`：通用工作台 + 子场景面板 + 功能模块卡片
- [x] 编写各专业工作台目录：`voucher`/`tax`/`financial_audit`/`sap_analysis`/`quality_trace`/`scheduling` 等（前端 `scenes/workbenches/*`，后端经 `scenes/renderer.py` + `scenes/workbenches/base.py` 承载）
- [x] 在 `scenes/registry.js` 或 `scenes/renderer.py` 按场景类型分发到对应工作台渲染器
- [x] 工作台文件导入（`import_config`）：文件以文本/base64 提交，保存到工作区临时目录供 Agent 读取（`api_workbench.SceneWorkbenchImportHandler`）
- [x] 工作台 ERP 元数据展示（`erp_config` 的 `systems`/查询入口），不携带真实连接凭据（`build_workbench_payload` 剔除连接字段）
- [x] 编写 `tests/test_scene_workbench.py` 与 `tests/test_scene_workbench_frontend.cjs`：通用/专业工作台渲染、子场景面板、文件导入、元数据剔除凭据
- [x] 运行对应测试确保工作台交互通过

## 阶段 5：接入点预留与文档

- [x] 在 `scenes/config.py` 的 `can_access_scene` 保留过滤结构（默认返回真），供后续 RBAC 接入
- [x] 确认 `scenes/scenes_config.json` 保留 `required_permission` 字段
- [x] 在 `scenes/` 定义数据表命名规范 `cj-{场景英文名}-{具体表名}`（跨场景 `cj-common-*`），写入设计文档；v1 核心路径不建表，仅固定约定
- [x] 更新 `docs/design/scene-application-retrofit-plan.md`，记录场景应用回流范围、独立目录结构、表命名规范、降级策略与未启用的 RBAC 接入点
- [x] 运行全量相关测试（后端 + 前端）确认无回归
- [ ] 归档本变更（`openspec archive`）前标记完成状态

## 依赖与验收

- 阶段 0、1、2、3、4 之间需按顺序落地，前一阶段联调通过后再进入下一阶段。
- 阶段 3/4 前端需以真实浏览器或前端断言验证侧栏、场景中心与工作台渲染。
- 验收不把隔离的前端验证表述为生产认证验收；端到端 ERP/SAP 数据接通留待后续变更。
