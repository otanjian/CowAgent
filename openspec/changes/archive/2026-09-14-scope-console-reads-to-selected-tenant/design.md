## Context

`channel/web/web_channel.py` 的 `_tenant_ids_for_context(ctx)` 是 database 模式下业务控制台读取范围的唯一来源，它同时被四处消费：

- `_iter_tenant_agents`（→ `_tenant_agents_projection` 工作台、`_tenant_agents_admin_projection` 配置页）
- `_require_session_owner`（会话读取门禁）
- `_list_sessions_across_agents`（历史检索）
- `channel/web/admin_overview.py`（控制台计数）

当前它在 `ctx.is_platform_admin` 时提前返回 `get_agent_registry().list(include_disabled=False)` 的**全量 id 列表**，因此平台管理员在业务控制台看到的范围与所选租户无关。与之相对，真正决定"能否发起对话"的发送路径 `_require_tenant_agent_binding` **没有**平台管理员旁路，它严格校验 `agent_bindings.tenant_id == ctx.tenant_id`，而工作台的就绪判定 `_workbench_chat_readiness` 走的是 `check_resource_action`（平台管理员 `all` 恒真）。两者口径不同，于是出现"卡片可点、发送被拒"。

约束：身份门禁 `resolve_context` 已要求非成员租户的选择返回 403，所以在控制台里平台管理员只能选择本人有效租户；跨租户能力必须走平台入口（`PlatformTenantAgentsHandler`，要求平台资格 + 近期密码 + 审计）。本 change 不触碰这一层。

## Goals / Non-Goals

**Goals:**

- 业务控制台的智能体可见范围、会话读取范围、历史检索范围与控制台计数，统一等于"当前所选租户"，平台管理员同样受此约束。
- 让"卡片可用状态"与"发送路径判定"使用同一个租户绑定前提，消除可点但被拒的窗口。
- 用 spec 把平台管理员在业务控制台的租户范围写明确，防止该分支被重新引入。

**Non-Goals:**

- 不改变身份模型与门禁：不放开平台管理员选择未加入租户（维持 `account-menu-actions` / `self-account-context` / `identity-session`）。
- 不新增/替换平台入口的跨租户能力，不改其审计与近期密码要求。
- 不改动租户选择器前端逻辑，不改动写路径（创建/编辑/归档/删除）的既有租户绑定校验。

## Decisions

**1. 收敛 helper，而不是在每个调用点加过滤。**
删除 `_tenant_ids_for_context` 的平台管理员分支，使其 database 模式下一律返回 `tenant_agent_ids(ctx.tenant_id)`，未选租户返回 `[]`；`ctx is None`（legacy）保持不设限。
- 理由：四个消费方共用同一入口，改一处即口径一致，避免"列表已收敛但计数/历史仍全量"的半收敛状态——这类不一致正是本次缺陷的成因。
- 备选：只改 `_iter_tenant_agents`（只收敛工作台）。否决：会让配置页、历史与计数继续跨租户，规范要求的"列表、总数与搜索先过滤后分页"仍被违反。
- 备选：保留全量名单但在投影层按租户过滤。否决：把租户边界交给最外层展示代码，任何新消费方都会再次越界。

**2. 在 `_workbench_chat_readiness` 前置"目标 Agent 必须绑定调用者租户"。**
收敛后该判断在正常路径恒真（可见集已 ⊆ 绑定集），保留它是为了让"列表说可用、发送说 404"不可能靠重新引入跨租户列表而复发。判定顺序：先绑定校验 → 再 `agent.use` → 再 `chat.use`，失败统一返回既有的稳定原因码 `permission_denied`（与发送路径的 404/403 语义一致，不新增面向用户的文案）。
- 理由：规范已要求"使用动作与发送路径的判定一致，不出现卡片可点但发送被拒"；把该不变量放在就绪判定里，比依赖上游过滤更抗回归。
- 备选：不加，仅依赖投影收敛。否决：一旦有人再次放开可见范围，缺陷会以同样的形态回归且无测试拦截。

**3. 平台管理员的跨租户智能体管理保持在平台入口。**
`/api/platform/tenants/<id>/agents` 不经 `_tenant_ids_for_context`，因此不受本次收敛影响；业务控制台不再承担跨租户查看/管理职责。
- 理由：符合 `platform-all-authorization`「专用平台入口 + 明确目标租户 + 审计，不伪造 Membership」。

## Risks / Trade-offs

- [平台管理员在业务控制台看不到其它租户的智能体，可能被当作功能回退] → 这是规范既定口径；`agent-workbench` 与 `tenant-resource-isolation` 的 delta 已把该行为写成显式要求，平台入口保留同等能力（含可复制候选），并在 proposal 中记录使用路径。
- [既有测试锁定旧行为，收敛后会失败] → `tests/test_tenant_default_agent.py` 的两条用例按新口径改写为"选中租户只含该租户绑定"与"未选租户为空"，并新增投影级回归用例；不删除断言，只改期望。
- [未选租户返回 `[]` 可能让既有调用方把空当"无权限"处理] → 未选租户在业务控制台本就不发起租户业务请求（`identity-session` 的每标签选择要求），且门禁对缺租户选择返回 400；返回空集是 fail-closed，不会放宽任何访问。
- [收敛影响面覆盖历史与计数，若某调用方此前依赖全量语义会改变输出] → 该语义本身违反规范（`session-history-workbench` 要求搜索"不扩大租户范围"）；已按"全部收敛"确定口径，并纳入回归验证。

## Migration Plan

无数据迁移。纯服务端读取范围收敛，无新字段、无接口签名变化：
1. 改 `_tenant_ids_for_context` 与 `_workbench_chat_readiness`；
2. 改写/新增测试；
3. 回归受影响用例与 `openspec validate --strict`。
回滚：还原两处函数即可，无持久化副作用（两函数均为只读）。

## Open Questions

无。口径（平台管理员在业务控制台跟随所选租户、跨租户走平台入口）已由用户确认，不涉及需要后续再定的实施参数。
