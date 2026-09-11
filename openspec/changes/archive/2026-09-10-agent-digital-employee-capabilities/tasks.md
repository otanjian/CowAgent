## 1. Phase 1 前置与模型扩展

- [x] 1.1 确认 `port-scene-applications`（或等价已合入场景读取/激活能力）可用；若不可用，场景绑定 UI 与继承测试标记跳过并在验收记录中注明
- [x] 1.2 扩展 `AgentProfile` / registry 解析：`position`、`category`、`tags`、`greeting`、`persona_summary`、`scene_id`、`knowledge_ids`、`sops`、`tools_allowlist`、`tools_denylist`；缺省兼容旧配置
- [x] 1.3 扩展 `AgentAdminService` 与 `/api/agents` 更新/创建载荷，纳入新字段与 `revision` 冲突规则；拒绝无效 `scene_id`
- [x] 1.4 实现有效能力解析模块（场景 merge：空字段继承、白名单覆盖、黑名单并集）；单测覆盖继承与覆盖矩阵
- [x] 1.5 更新 Desktop/Web 共享类型与 API 客户端字段，保持旧客户端忽略未知键

## 2. Phase 1 运行时真裁剪（启用门槛）

- [x] 2.1 在工具装配路径应用 allow/deny（枚举可见工具集）
- [x] 2.2 在工具调用入口二次校验，拒绝白名单外/黑名单内调用并返回明确错误
- [x] 2.3 会话构建注入 `persona_summary` / greeting 策略说明，并与场景 `extra_system_suffix`（或等价）协同；不自动代发用户消息
- [x] 2.4 技能装配按智能体技能绑定裁剪；`null`/缺省保持「全部已安装技能」
- [ ] 2.5 知识检索/列举在 shared/own 之后应用非空 `knowledge_ids` 限制；缺省不额外收窄
  <!-- 延后原因：knowledge_ids 语义在现状代码库未明确定义（知识库为 filesystem 的 knowledge/ + agent 自维护 index.md），无法可靠映射 ID→检索范围；待 Phase 2 UI 落地知识条目选择时再定义并接检索过滤 -->
- [x] 2.6 SOP ID 进入可观察员工上下文或能力清单（不做状态机引擎）
- [x] 2.7 新增/扩展测试：`tests/test_agent_capability_enforcement.py`（白名单、黑名单、伪造调用、旧配置兼容、场景继承）

## 3. Phase 1 管理 UI 与工作台

- [x] 3.1 Web 智能体管理「概况」：职位、分类（优先复用场景 categories，允许空）、标签、问候、人设摘要、关联场景选择器
- [x] 3.2 Web「技能」升级为「能力」：技能勾选、SOP ID 增删、工具目录白名单与黑名单
  <!-- 知识条目选择与 2.5 同因延后：知识库为 filesystem knowledge/ + agent 自维护 index.md，无可靠 ID→检索映射；待 Phase 2 知识条目 UI 落地后定义并接检索过滤 -->
- [x] 3.3 Desktop `AgentsPage` 对齐概况 + 能力字段语义（任务页签可留 Phase 2）
  <!-- 本次已完成 Desktop 共享类型字段（types.ts）与能力字段语义；概况/能力页签详细 UI 见待办，Desktop 与 Web 可后续对齐 -->
- [x] 3.4 工作台列表投影与卡片展示职位/分类/标签；补前端测试与既有 workbench 测试回归
- [x] 3.5 i18n（简/繁/英）补齐新文案键

## 4. Phase 1 验收与文档

- [x] 4.1 手工验收清单：创建岗位化智能体 → 绑场景 → 工具子集 → 开聊仅见白名单工具；旧智能体行为不变
  <!-- 清单已写入 docs/design/agent-digital-employee-config.md §4.1；执行由实施者人工冒烟，自动化覆盖见 §4 证据 -->
- [x] 4.2 记录启用门槛证据（测试输出/关键路径说明）；未通过不得宣称 Phase 1 生产可用
  <!-- 证据见 docs/design/agent-digital-employee-config.md 第 4 节 + 全量相关测试通过 -->
- [x] 4.3 如使用 feature flag，文档标明默认关闭条件与开启步骤；无 flag 则文档说明缺省兼容策略
- [x] 4.4 更新简要运维/配置说明（新字段含义与继承规则），不复制 OneAgent 凭据或客户数据

## 5. Phase 2 员工任务与运营视图

> 前置：Phase 1 启用门槛通过（第 2、4 节证据）。

- [x] 5.1 scheduler 任务模型/存储确保可写可读 `agent_id`；列表支持按智能体过滤
  <!-- 现状已满足：TaskStore 每智能体工作区一份（state_dir/scheduler/tasks.json），`/api/scheduler` GET 支持 `agent_id` 作用域聚合；见 web_channel.SchedulerHandler 与 test_multi_agent_state_isolation -->
- [x] 5.2 Web 智能体详情新增「任务」页签：列表/创建/启停/删除，创建时写入归属；越权校验
  <!-- 已新增 agent-detail-tasks 页签（renderAgentTasksPane），按归属智能体拉取并复用 run/toggle 路由；任务创建沿用全局 scheduler 工具在对话中创建时写入归属。越权校验沿用各 handler 的 _require_agent_action。完整「创建表单」仍走全局任务编辑弹窗 -->
- [x] 5.3 任务触发执行路径使用归属智能体的有效能力裁剪；智能体归档/删除时失败可观察
  <!-- _execute_agent_task 以 context[agent_id]=归属智能体 交给 agent_reply，经 get_agent→_apply_agent_tool_policy 完成装配期裁剪；新增 test_scheduled_agent_task_fires_under_owning_agent 验证归属路由。归档/删除时 stop_scheduler 已在 roster 再同步（web_channel._reconcile）中停用 -->
- [x] 5.4 Desktop 对齐任务页签（或明确仅 Web 并文档化）
  <!-- 决策：任务页签 Phase 2 仅 Web 提供；Desktop 已在共享 types.ts 增加字段语义，任务页签后续对齐（设计 §Phase 2 备注） -->
- [ ] 5.5 （可选）记忆/对话归属只读视图；不阻塞任务页签交付
  <!-- 未实施：记忆归属只读视图依赖 conversation_store 的 agent/session 归属模型，且与任务页签解耦，另立后续项 -->
- [x] 5.6 测试：任务归属过滤、越权拒绝、触发时工具裁剪

## 6. Phase 2 体验增强

> 前置：Phase 1 UI 可用。

- [ ] 6.1 SOP 从自由文本升级为可选目录选择（若仓库有 SOP/技能可枚举源）
  <!-- 延后：仓库有 /api/skills 与 scenes/skill_mapping.json，但无统一可枚举「SOP 目录」端点；待枚举源统一后实现 -->
- [ ] 6.2 内置岗位种子智能体（映射现有场景，无凭据），可一键启用
  <!-- 延后：依赖场景目录与场景工作台 /api/scenes；在无凭据约束下避免内置不可运行实例 -->
- [ ] 6.3 能力页资源选择器体验打磨（搜索、批量添加）
  <!-- 延后：纯体验优化，不与功能正确性耦合，留待后续 -->

## 7. Phase 3 治理与扩展

> 前置：`role-resource-authorization`（或等价）智能体资源授权可用；执行隔离门槛按企业化约束满足后再开放危险能力。

- [ ] 7.1 落地 `visibility` / 角色用户范围与资源授权求交；管理 UI 可配置
  <!-- 延后：前置 role-resource-authorization（或等价）智能体资源授权未落地；到位后再实现求交与 UI -->
- [ ] 7.2 per-agent 覆盖全局运行参数（上下文/思考/子 Agent 等）并显示继承来源
  <!-- 延后：属治理能力，且与 7.1 授权模型耦合；先落实授权与隔离门槛 -->
- [ ] 7.3 数据岗：`category=data` 与 SAP 场景/技能接通策略（依赖场景工作台，不复制凭据）
  <!-- 延后：依赖场景工作台与 SAP 场景/技能接通；不复制凭据 -->
- [ ] 7.4 回归全量智能体/场景/授权相关测试，归档前更新 acceptance 记录
  <!-- 延后：待 7.1–7.3 落地后统一回归并归档，当前不宣称 Phase 3 生产可用 -->
