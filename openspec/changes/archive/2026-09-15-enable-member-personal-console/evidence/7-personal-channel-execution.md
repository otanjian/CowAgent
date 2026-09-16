# 7 个人渠道执行启用证据

对应 `personal-channel-configuration` 的「个人路由入站裁决」「公共绑定的默认智能体」
「失效连接即时关闭」与 `user-personal-context` 的「私有会话不注入共享上下文」相关 requirement。

用例：`tests/test_personal_channel_inbound.py`（38 项，含 4 项开关姿态）；相邻回归
`tests/test_personal_channel_console.py`（54）+ `tests/test_personal_channel_binding.py`（31）
全绿；公共入站/默认智能体/租户网关相邻档 192 项 + 4 subtests 全绿。

## 1. 落地实现

| 关注点 | 位置 | 说明 |
| --- | --- | --- |
| 唯一入站裁决点 | `auth/service.py::resolve_personal_channel_inbound` | 返回 `{allowed, reason, owner_user_id, agent_id}`，**从不抛异常**：调用方在拒绝一条消息，需要的是可行动的理由而不是栈 |
| 共享实例个人路由 | `auth/service.py::resolve_shared_personal_route` | `None` = 无人认领（走公共行为）；非 `None` 而不允许 = 拒绝，绝不换回共享 Agent |
| tenant 取自实例 | `resolve_personal_channel_inbound` | `tenant_id` 只从实例行读取，发送者三元组无法改变它 |
| 三元组仍有效 | `_identity_still_resolves` | 路由记录「当时谁证明了该账号」，每条消息重读绑定；被改绑/撤销即失效 |
| 目标重新归属 | `is_private_agent_owner` + `_member_can_use_agent` | 目标必须是**本人现在仍拥有的**私有 Agent，且本人仍有 `agent.use`；两层分别拒绝 |
| 共享实例路由解析 | `channel/external_identity.py::personal_route_for` + `instance_row` / `is_personal_instance` | 实例作用域与 owner 由实例行决定，不信任消息内容 |
| 拒绝理由固定化 | `channel/external_identity.py::personal_deny_reason` | 未知 verdict 一律按「不可用」处理，不按「放行」处理 |
| 入站接入 | `channel/chat_channel.py` | `_preflight_personal_inbound` / `_serve_personal_route` / `_scope_to_member` / `_redeem_route_code` |
| 挑战码不落地 | `_redeem_route_code` | 绑定码在其自身消息里消费，消息不再进入 Agent，避免码被转述给智能体 |
| 公共默认智能体 | `auth/service.py::resolved_public_default_agent_id` | 缺省公共绑定只解析同租户**公共**智能体；无则 `None`（拒绝），不回退全局默认或成员私有 Agent |
| 失效连接即时关闭 | `auth/service.py::_reconcile_personal_runtime` | 接入治理停用/凭证撤销/解绑/删除身份/成员停用五条路径 |
| 成员停用关连接 | `channel/channel_instances.py::_owner_is_active_member` | user 作用域实例在运行时也校验 owner 仍为本租户有效成员 |
| 执行开关 | `channel/channel_instances.py` | `PERSONAL_RUNTIME_ACCEPTED_TYPES` / `PUBLIC_PERSONAL_INGRESS_TYPES`，出厂 `frozenset()`；`personal_runtime_enabled` 在运行期与请求期各判一次 |

## 2. 需求 → 用例

| 需求场景（7.1 入站验证） | 用例 |
| --- | --- |
| owner 私聊经本人 Agent 执行 | `test_the_owner_runs_as_themselves_through_their_own_agent` |
| 他人发送者绝不被当作 owner 服务 | `test_another_sender_is_refused_and_never_served_as_the_owner` |
| 实例无 owner 时谁都不服务 | `test_an_instance_whose_owner_is_gone_serves_nobody` |
| 个人路由绝不回退到另一个 Agent | `test_a_personal_route_never_falls_back_to_another_agent` |
| 跨租户发送者够不到个人实例 | `test_a_cross_tenant_sender_cannot_reach_a_personal_instance` |
| 路由命中的三元组被改绑即失效 | `test_a_rebound_account_loses_the_route_it_proved` |
| 目标 Agent 被重归属时拒绝 | `test_the_ownership_loss_is_its_own_refusal_not_a_permission_verdict` |

| 需求场景（7.2 公共绑定与显式个人路由） | 用例 |
| --- | --- |
| 共享机器人上的已验证私聊用本人 Agent | `test_a_verified_private_chat_on_a_shared_bot_uses_the_members_own_agent` |
| 同一共享机器人按成员分别路由 | `test_a_shared_route_is_per_member` |
| 普通发送者仍由共享 Agent 回答 | `test_an_ordinary_author_keeps_the_shared_agent` |
| 无路由/无授权时按公共规则拒绝 | `test_a_member_without_a_route_gets_the_public_answer` |
| 个人路由失败不回退共享 Agent | `test_a_shared_route_never_falls_back_to_the_shared_agent` |
| 群聊忽略个人路由 | `test_a_group_on_a_shared_bot_ignores_personal_routes` |
| 群聊不进私有 Agent | `test_a_group_message_never_reaches_a_private_agent` |
| 公共缺省不回退成员私有 Agent | `test_a_public_instance_never_defaults_to_a_members_private_agent` |
| 公共缺省取本租户公共 Agent | `test_the_public_default_is_the_tenants_shared_agent` |
| 跨租户发送者不是共享机器人成员 | `test_a_cross_tenant_sender_is_not_a_member_of_the_shared_bot` |

| 需求场景（7.3 失效即时生效） | 用例 |
| --- | --- |
| 治理停用 | `test_governance_stop_takes_effect_on_the_next_message` |
| 治理停用是独立闸门（非 active 别名） | `test_the_governance_stop_is_its_own_gate_not_the_active_flag` |
| 凭证撤销 | `test_a_revoked_credential_stops_the_next_message` |
| 解绑 | `test_unlinking_stops_the_next_message` |
| 身份解绑 | `test_an_unbound_identity_stops_the_next_message` |
| 成员停用 | `test_a_deactivated_member_stops_the_next_message` |
| 成员停用关闭连接而非仅拒绝 | `test_a_deactivated_members_instance_is_stopped_not_just_refused` |
| 渠道类型未验收 | `test_an_unaccepted_channel_type_serves_nothing` |
| Agent 不再属于本人 | `test_an_agent_that_is_no_longer_the_owners_is_refused` |

| 需求场景（7.5 开关姿态） | 用例 |
| --- | --- |
| 出厂开关关闭 | `TestPersonalExecutionSwitch::test_the_shipped_switch_is_closed` |
| 配置只能收窄不能放宽 | `test_configuration_can_only_narrow_the_switch` |
| 未配置时保持声明集合 | `test_an_unset_configuration_keeps_the_declared_set` |
| 共享路由需 ingress 开关 | `test_a_shared_route_needs_the_ingress_switch_on` |

| 需求场景（7.4 真实链路） | 用例 |
| --- | --- |
| owner 的运行到达真实工具分发（身份一致） | `test_the_owners_run_reaches_the_real_tool_dispatch_as_the_owner` |
| 被拒绝的消息不产生可分发身份 | `test_a_refused_message_never_yields_a_dispatchable_identity` |
| 绑定码不从消息进入 Agent | `test_the_code_is_never_handed_to_the_agent_even_when_it_works` |
| 错码不留下任何路由 | `test_a_wrong_code_leaves_no_route_behind` |
| 共享实例的码只绑定证明者本人 | `test_a_shared_instance_code_links_only_the_sender_who_proved_it` |
| 路由只能指向本人私有 Agent | `test_a_route_may_only_target_the_members_own_private_agent` |
| 不能指向他人私有 Agent | `test_a_route_cannot_target_another_members_private_agent` |

## 3. 变异检查（8/8 被捕获）

对每条 requirement 抽一处守卫做定向变异，再看对应用例是否失败；全部 8 项均被捕获：

| 变异 | 目标用例 | 结果 |
| --- | --- | --- |
| M1 陈旧路由在其账号被改绑后仍存活 | `test_a_rebound_account_loses_the_route_it_proved` | 捕获 |
| M2 owner 机器人上任何发送者都被当作 owner | `test_another_sender_is_refused_and_never_served_as_the_owner` | 捕获 |
| M3 目标不再重新归属校验 | `test_the_ownership_loss_is_its_own_refusal_not_a_permission_verdict` | 捕获 |
| M4 共享路由检测失效（回退公共 Agent） | `test_a_shared_route_never_falls_back_to_the_shared_agent` | 捕获 |
| M5 出厂执行开关被打开 | `test_the_shipped_switch_is_closed` | 捕获 |
| M6 公共缺省借用成员私有 Agent | `test_a_public_instance_never_defaults_to_a_members_private_agent` | 捕获 |
| M7 群聊被当作 owner 私聊 | `test_a_group_message_never_reaches_a_private_agent` | 捕获 |
| M8 治理停用不再优先 | `test_the_governance_stop_is_its_own_gate_not_the_active_flag` | 捕获 |

首轮 5/8：M1/M3/M8 逃逸，三处都暴露了「拒绝存在，但拒绝的原因不对」——用户可见
通知被合并（M3 的两种拒绝映射到同一张卡片），或被同事务的清理与 `active=0` 遮蔽
（M1/M8）。补的三个用例因此改为直接断言 verdict `reason` 与状态组合，把每一层
各自钉住，而不是只钉最终观感。

## 4. 尚未验收的范围（保持开关关闭）

出厂 `PERSONAL_RUNTIME_ACCEPTED_TYPES = frozenset()`、
`PUBLIC_PERSONAL_INGRESS_TYPES = frozenset()`，即：

- 个人渠道实例可以**配置**并显示连接状态，但不会真正接入并执行；
- 共享实例不会承载个人路由；
- 请求期再判一次，所以关掉开关不会留下「已连接还在服务」的窗口。

待真机验收：飞书、企业微信、钉钉三类个人接入的端到端接入与消息投递
（含挑战码私聊、群聊排除、被治理停用后的连接关闭）。验收通过前不修改上述默认值；
本项因此以「关闭 + 准确记录待验收范围」分支结项，而非以打桩用例声明生产能力已开放。
