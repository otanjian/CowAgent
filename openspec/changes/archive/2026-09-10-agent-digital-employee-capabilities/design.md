## Context

See proposal.md — Why。当前 CowAgent 以 `AgentProfile`（`agent/registry.py`）管理多智能体：名称、描述、workspace、model、skills allow-list、knowledge 选择/ shared·own，管理 UI 为概况/技能/核心文件三页签；工作台为只读卡片（`agent-workbench`）。全局「Agent 配置」管上下文上限、思考、子 Agent、进化与权限模式，非 per-agent。场景应用由 `port-scene-applications` 引入 `scenes/` 与激活注入；尚未与 Agent 档案绑定。OneAgent 的 `agent/employee` 为参考实现，含岗位字段、四类资源、场景 merge、员工任务/记忆页签；其 `tools_allowlist` 在参考代码中注入不完全等于 ToolManager 硬过滤——本设计要求 CowAgent 做真过滤。

约束：不平行员工名册；保留 `/api/agents` 兼容与 revision；database 模式授权每次解析；多租户任意代码默认禁用直至执行隔离验收；OneAgent 不复制凭据与客户数据。

## Goals / Non-Goals

**Goals:**

- 在 `AgentProfile` 上扩展数字员工核心配置，并贯通管理 UI → 持久化 → 会话运行时裁剪。
- 场景作为岗位模板：`scene_id` + 明确继承/覆盖规则。
- 工具 allow/deny 在枚举与调用双路径强制生效。
- 工作台卡片展示岗位化只读信息；员工详情可管理归属定时任务。

**Non-Goals:**

- 不新建 `employees.json` / 第二套 ID。
- 不引入普通/深度代理类型。
- 不做完整 SOP 状态机引擎（Phase 1 仅绑定 ID + 上下文暴露）。
- 不在本 change 完成完整 visibility UI 与角色资源授权产品化（仅字段与过滤接入点）。
- 不做 per-agent 覆盖全局上下文/思考/子 Agent（列入后续）；不接通 SAP 数据岗端到端。
- 不搬运 StaffDeck / OneAgent 前端整包。

## Decisions

### D1: 演进 AgentProfile，而非平行 Employee 模块
- **选择**：扩展现有 registry/admin/API。
- **理由**：CowAgent 已有 workspace、委派、核心文件与工作台双入口；双名册会分裂会话归属与渠道绑定。
- **备选**：照搬 `agent/employee/`（弃用，双轨）；仅场景皮肤（弃用，无持久装配）。

### D2: 字段落在 agents 配置，继承在解析层计算
- **选择**：持久化员工显式字段；`EffectiveAgentCapabilities`（或等价）在读取/会话构建时 merge scene。
- **理由**：避免把场景快照写死进员工配置，场景更新可被空字段继承；非空员工字段稳定覆盖。
- **备选**：保存时物化展开（不利于场景更新同步）。

### D3: 工具裁剪落在 ToolManager（或统一装配门面）
- **选择**：构建 Agent 工具表时应用 allow/deny；调用入口二次校验。
- **理由**：OneAgent 参考实现存在「配置有、执行弱」风险；数字员工核心是真裁剪。
- **备选**：仅 prompt 告知模型勿调用（不可接受）。

### D4: 知识 `knowledge_ids` 与 shared/own 并存
- **选择**：shared/own 管隔离根；`knowledge_ids` 非空时再限制条目集。
- **理由**：保留已交付知识模式，避免破坏现网。
- **备选**：用条目绑定完全替代 shared/own（迁移成本高，本 change 不做）。

### D5: UI 结构
- **选择**：概况扩展字段；原「技能」升级为「能力」四块；新增「任务」页签；核心文件不变。Web + Desktop 语义对齐。
- **理由**：对用户仍是一个智能体对象，符合菜单「智能体管理」定位。

### D6: 分期门槛
- **Phase 1（启用门槛）**：D1–D5 的配置 + 运行时工具/技能/知识裁剪 + 工作台岗位字段 + 场景绑定继承；自动化测试覆盖过滤与继承。
- **Phase 2**：员工任务页签与 scheduler `agent_id` 贯通、SOP 选择体验、记忆/对话归属只读视图、岗位种子。
- **Phase 3**：visibility 与 `role-resource-authorization` 求交、per-agent 运行参数、数据岗场景接通。
- Phase 2/3 任务标记前置；未达门槛不得宣称能力已生产可用。

### D7: 接入点（实现时）
| 区域 | 路径 |
|------|------|
| 模型 | `agent/registry.py`、`agent/admin.py` |
| 继承/有效能力 | 新小组件为宜，如 `agent/effective_capabilities.py`（名称可调） |
| 工具过滤 | `agent/tools/tool_manager.py` 及 Agent 初始化路径 |
| 提示注入 | `bridge/agent_bridge.py` / `agent/protocol/agent.py` / prompt builder |
| API | `channel/web/web_channel.py` Agents* handlers |
| Web UI | `console.js` 智能体管理 + 工作台卡片 |
| Desktop | `AgentsPage.tsx`、类型定义 |
| 场景 | `scenes` 读取 API；激活可与员工绑定协同 |
| 任务 | scheduler store + `/api/scheduler*`，写入 `agent_id` |

## Risks / Trade-offs

- **[Risk] 白名单过严导致旧自动化失效** → Phase 1 缺省不设白名单；迁移文档强调显式配置才裁剪。
- **[Risk] 场景 change 未合入** → 场景绑定任务标注前置 `port-scene-applications`；无场景时 UI 隐藏或禁用绑定。
- **[Risk] 工具名不稳定/别名** → 以 `/api/tools` 当前注册名为准；未知名保存时告警或拒绝。
- **[Risk] 管理 UI 膨胀** → 能力页分块，SOP/记忆二期再加重交互。
- **[Trade-off] SOP 仅 ID** → 先满足装配模型，状态机另开 change，避免本 change 范围爆炸。
- **[Trade-off] 可见性仅接入点** → 避免与未完成 RBAC 产品抢进度，但字段先预留降低返工。

## Migration Plan

1. 配置 schema 向前兼容：新键缺省为空/`null`，加载旧 `agents` 不迁移文件即可运行。
2. 先落地后端有效能力解析与 ToolManager 过滤 + 测试，再开管理 UI。
3. 工作台投影加字段，旧客户端忽略未知键。
4. 回滚：关闭新 UI 入口（若有 flag）或忽略新键；过滤逻辑在「无新键」时 no-op，可安全回退版本。
5. 不写破坏性数据迁移；不删除核心文件模型。

## Open Questions

- 分类枚举是否直接复用 `scenes_config.json` 的 categories，还是允许自定义自由文本（建议 Phase 1 复用场景分类 + 允许空）。
- 问候语是仅 UI 展示，还是首次开聊自动发送（建议 Phase 1 仅注入策略/展示，不自动发用户消息）。
- Desktop 是否与 Web 同步「任务」页签（建议 Phase 2 对齐齐，Phase 1 Desktop 至少概况+能力）。
