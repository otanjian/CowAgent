## Why

平台管理员在业务控制台（顶部租户选择器）选中「默认租户」时，智能体列表仍会列出其它租户绑定的智能体（`business-analysis-test15`、`knowledge-qa-test15`、`my-assistant-admin-test15`），且这些卡片被标记为可发起对话。根因是 `_tenant_ids_for_context` 为平台管理员返回**整个启用名单**，与既定规范冲突：`agent-workbench` 要求 database 模式「按有效租户、成员和智能体读取授权过滤」，`tenant-resource-isolation` 要求「租户列表和默认 Agent 选择 SHALL 只在当前租户内进行」，`platform-all-authorization` 要求平台 `all`「仍验证……**原数据范围**」。这条分支还同时驱动会话读取门禁、历史检索与控制台计数，使四个读取路径一起越出租户。

用户已确认口径：**不改变身份模型**——平台管理员在业务控制台内的可见范围跟随所选租户；跨租户的看/管智能体继续走「专用平台入口 + 明确目标租户 + 审计」（`/api/platform/tenants/<id>/agents`）。本 change 让代码回到既有规范，并把「平台管理员在业务控制台同样受当前租户约束」这一点在 spec 中写明确，避免同一条分支再次被重新引入。

## What Changes

- `_tenant_ids_for_context`：删除平台管理员返回全量名单的分支。database 模式下一律返回调用者**当前所选租户**的绑定集合；未选租户返回空集（fail-closed）；legacy（`ctx is None`）保持不设限。
- 工作台与配置页的智能体投影、会话读取门禁 `_require_session_owner`、历史检索 `_list_sessions_across_agents`、控制台概览计数一起收敛到同一口径（共用该 helper，不存在第二套过滤）。
- `_workbench_chat_readiness` 增加「目标智能体必须绑定调用者租户」的前置判断：收敛后该判断在正常路径上恒真，用途是让「卡片可点但发送被 403/404 拒绝」这类不一致无法靠重新引入跨租户列表而复发（对应 `agent-workbench` 既有的「使用动作与发送路径判定一致」要求）。
- spec 收敛（MODIFIED）：`agent-workbench`「最小读取与身份范围」、`tenant-resource-isolation`「Agent 租户绑定与现有标识保持稳定」——明确平台管理员在业务控制台的智能体读取同样只在当前租户内，跨租户管理经平台入口。
- 不改动：平台入口跨租户能力（`PlatformTenantAgentsHandler`）、租户选择器只列本人有效租户（维持 `account-menu-actions` / `self-account-context`）、身份门禁对非成员租户选择返回 403（`resolve_context`）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-workbench`: 「最小读取与身份范围」补明读取范围就是调用者**当前所选租户**，平台管理员不例外；跨租户查看/管理不属于业务控制台职责，由平台入口承担。
- `tenant-resource-isolation`: 「Agent 租户绑定与现有标识保持稳定」补明同一约束同样约束平台管理员的业务控制台读取，并明确未选租户时返回空列表而非全量。

## Impact

- 代码：`channel/web/web_channel.py`（`_tenant_ids_for_context`、`_workbench_chat_readiness`）；消费方 `channel/web/admin_overview.py`、`_require_session_owner`、`_list_sessions_across_agents` 无需改动即可随 helper 收敛。
- 行为变化：平台管理员在业务控制台选中某租户时，只看到该租户绑定的智能体与相关计数；查看其它租户的智能体需经平台控制台「租户编辑 → 智能体」标签。
- 测试：`tests/test_tenant_default_agent.py` 中两条锁定旧行为的用例需按新口径改写，并新增「平台管理员选中租户下的工作台投影只含该租户智能体」的回归用例。
- 依赖：不新增 capability，不引入新接口、迁移或数据字段；无跨 change 前置。
