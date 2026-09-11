# 验收证据汇总（5.3）

> 2026-09-08 独立复验发现布局、启动校验及状态提示等缺陷并已修复。最新结论、测试范围及服务重启限制以 [独立验收与修复记录](review-and-fixes.md) 为准；下文保留为原实施记录。

## 已交付并验证

| # | 验证项 | 方法 | 证据 |
| --- | --- | --- | --- |
| 导航拆分 | 工作台 → 智能体（`agent-workbench`）、管理 → 智能体配置（`agents`） | 浏览器 DOM 遍历 | 菜单分组确认：工作台含 `chat/history/agent-workbench/todo/tasks/scenarios`；管理含 `config/agents/skills/...` |
| 白名单投影 | `GET /api/agents?view=workbench` | 浏览器 + curl + pytest | 返回 `{id,name,description,avatar,is_default,can_chat,unavailable_reason}`，不含 `workspace/channel_instances/revision`（`test_view_workbench_returns_whitelisted_fields_only`） |
| 旧 API 契约 | `GET /api/agents`（无参） | pytest | 返回完整快照（`test_default_view_returns_full_snapshot`），Desktop `RosterSnapshot` 契约不变 |
| 卡片刻画 | 默认项置顶 + `默认` 徽标 + `开始对话` | 浏览器截图（简中/繁中/英文） | 「RongAI(default)/default/默认」与「ERPnext助手/erpnext」卡片均正确渲染 |
| 启动闭环 | 点击「开始对话」→ 新会话绑定目标智能体 | 浏览器交互 | `localStorage.cow_active_agent = "erpnext"`；chat 视图激活；chat-header 展示「ERPnext助手」身份；输入框聚焦 |
| 输入框聚焦 | 进入后自动聚焦 | 浏览器 | `focusChatComposer()` 调用 `requestAnimationFrame(()=>input.focus())`；`chatInput` 状态 active/focused |
| 键盘操作 | 卡片 Tab 可聚焦 + Enter 激活 | 浏览器 | 卡片为单一 `<button>`，`tabIndex:0`；Enter 触发 `activeAgent="default"` 并进入 chat |
| 窄屏响应式 | 375px 下卡片单列堆叠 | 浏览器 Emulation | `gridTemplateColumns` = `347px`（单列），卡片全宽 |
| 加载/空/失败/重试 | 失败显示错误样式 + 「重试」按钮 | 浏览器 fetch mock | 失败 → `statusError=true, retryBtn="重試", cardCount=0`；重试恢复 → 2 卡、错误清除 |
| 不可用智能体 | `can_chat=false` 禁启动 | 浏览器 fetch mock | `id=offline, disabled=true, onclick=null, action="暫不可用"`；不静默回退默认 |
| i18n | 三语文案 | 浏览器 + grep | 10 个新 key 各出现 3 次（简中/繁中/英文）；浏览器实际渲染「智能体/智慧體/Agents」「開始對話/Start chat」 |
| 后端回归 | 不影响既有能力 | pytest 逐文件 | web_management(4)、routing(7)、multi_agent_isolation(12)、agent_admin(29)、workspace_edit(25)、doc_edit(25)、cli_backup(16)、agent_registry(17)、feishu_static_card(3)、agent_workbench(10) 全绿 |
| 只读投影无副作用 | 读取不改活动会话 | 代码审查 + 测试 | `_workbench_agents_projection()` 仅 `registry.list()`，无状态写入 |

## 未覆盖 / 限制模式

- **database 身份模式未交付**：本期仅 legacy（共享密码）。`_workbench_chat_readiness()` 为将来 database 模式预留 hook；当前恒返回 `(True, None)`。未申报 database 可交付（满足「未具备 database 依赖时不得声明该模式可交付」）。
- **跨租户 / 无读取资格 / 失效会话 / 头像访问**：属 database 身份域，legacy 下无对应路径，不在本期范围内（4.2 已注明条件）。
- **`test_evaluation`（evolution）**：无测试用例收集（历史遗留空文件）。

## 独立 change 边界

- 共享工作树内的其它未提交 change（branding、tenant identity、auth、todos、prd-02 等）与本 change 独立。`test_branding.py` + `test_openai_chat_api.py` 的组合失败源于模块级 `web` stub 污染（各自独立运行全绿），不属于本 change。
- 本 change 未新增 feature flag；未修改 Agent 数据模型；未删除任何已生成会话。

## 归档后处理

实现完成后归档：

1. 在导航主规范中合并旧「管理菜单限制」，避免与本次双入口要求矛盾（`prd-02-platform-navigation` 的 `platform-navigation` spec 若含旧限制需修订）。
2. 保持其它 change 的独立任务状态与 `.openspec.yaml` 不变。

## 证据文件

- `evidence/menu-navigation-baseline.md`（阶段 A 菜单基线）
- `evidence/data-ownership-baseline.md`（阶段 A 数据归属基线）
- `evidence/mode-acceptance-record.md`（4.6 模式验收记录）
- `evidence/data-ownership-compat-verify.md`（5.1 数据归属与兼容边界核验）
