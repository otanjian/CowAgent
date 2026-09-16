# 阶段 5.1 / 5.2：存量无效目标的修复与关闭闭环

本文件记录 `tasks.md` 5.1、5.2 的落地位置与可复跑证据。结论来自当前工作区的实际代码与命令输出。
需求基线是 `openspec/changes/upgrade-personal-channel-workbench/specs/personal-channel-workbench/spec.md`
（「存量个人实例可修复且公共路由保持兼容」），不引用任何 PRD 编号。

## A. 改动前的基线（先记录，后动手）

### A.1 读取代码得到的既有事实

**已经具备（5.1 / 5.2 的大部分只读部分）**，均在 `auth/service.py`：

- `_require_personal_instance_agent` 是唯一判据：非空 → 同租户 → 本人私有 → 注册表内存在且启用 → owner 仍持有 `agent.use`；
  空目标 `personal_agent_required`(400)，他人/跨租户/不存在 `personal_agent_forbidden`(403)，本人但停用 `personal_agent_disabled`(403)。
- `_personal_target_projection` 在不抛错的前提下把存量目标分类为 `ok` / `missing` / `invalid` / `disabled`，
  且只在归属成立后投影 `name`，非本人目标不泄露名称。
- `_personal_channel_projection` 据此给出 `actions`：无效目标时 `enable`/`bind` 为 false，
  `edit`/`disable`/`revoke`/`unbind` 仍按行状态给出（关闭动词不经目标判据），并已 advertise `repair_target`。
- 写路径已经允许“改名不动目标”：`update_tenant_channel_instance` 对 `scope='user'` 只在
  `new_agent != row["agent_id"] or credentials is not None` 时复检目标，纯改名不触发。
- 存量无效目标仍然可见：列表/单条读取都基于 `_store` 的原始行，不重建、不跳过。

**确实缺失（5.1 / 5.2 的缺口）**：

1. `actions["repair_target"]` 只是承诺，服务端没有对应动词，`PersonalChannelInstanceHandler.POST`
   只派发 `update` / `enable|disable` / `revoke` / `start_binding` / `unlink`。
   但**控制台契约已经定死**：`channel/web/static/js/personal-console.js` 的
   `personalActionRequest('personal-channels', 'repair_target', …)` 返回
   `{action: 'update', expected_version, agent_id, recent_password}`，只带目标与密码，
   不带 `display_name`、不带 `credentials`；`tests/test_personal_console_frontend.cjs`
   （「the repair verb is reachable and is an update that names a target」）把它固定下来。
   即：修复动作**本来就是**那条 `update` 写路径，缺口在服务端对它不够严。
2. `update_tenant_channel_instance` 对个人行的空目标判定是“与库中值相同就跳过”，因此**存量空目标行**
   在被显式写入空目标（`agent_id=""`）时会整笔通过并 `version+1`：空目标是畸形写入，
   却不会被拒，控制台可以把一次“没修的修复”报告成成功。
3. 个人行的预检比个人判据更弱：`allow_owner=True` 时先跑公共的 `_require_instance_agent`，
   于是跨租户/不存在的目标被回答成 `forbidden` /「agent is not available to this tenant」，
   而不是个人面的 `personal_agent_forbidden`——同一个非法目标会因“从哪扇门进来”得到不同解释，
   与控制台 picker 给出的拒绝理由不一致。
4. 修复“不改归属/不动凭据/不换默认”、以及“无效目标行仍可关闭、读取不自动重建”的性质没有专门测试。

### A.2 基线命令与输出（改动前）

```
.venv/bin/python -m pytest tests/test_personal_channel_console.py tests/test_personal_channel_binding.py -q
→ 15 failed, 102 passed, 5 subtests passed in 82.66s
```

15 个失败当时全部落在 `tests/test_personal_channel_binding.py`。**该失败不可复现**：同一命令现在为
`117 passed`，`tests/test_personal_channel_binding.py` 单独运行为 `31 passed`，
在与本阶段相关的 8 个文件合并运行中也是 `307 passed`。当时 `tests/_helpers.py` 正被另一条工作流
改动（`personal_target_roster` / `personal_channel_target` 为未提交新增），判定为并发编辑/并发运行
造成的瞬时污染，与本阶段改动无关；未能复现原始失败，故不把它当作本阶段的证据。

## B. 设计决定：不新增专用动词——修复就是那条 `update`

**最小值的那条路是让既有 `update` 成为修复动作，而不是再加一个 `repair_personal_channel_target`。**

理由（按分量排序）：

1. **契约已经如此**。控制台把 `repair_target` 映射成 `action: 'update'` + `agent_id`，
   并由 `tests/test_personal_console_frontend.cjs` 固定。服务端若另开专用动词，就必须同时改
   dispatch、改前端映射或加一条转发——三处要一直保持一致，而目标本来就只有一个字段（实例的 `agent_id`）。
2. **`actions["repair_target"]` 与可调用动作天然一致**。投影 advertise 的是一个**能力**
   （“这一行可以换目标”），不是 HTTP 动词名；它对应的调用就是 `update` 带 `agent_id`，
   服务端不存在“假承诺”。
3. **关闭语义自动继承**。`update` 已经实现“改名不动目标”（`agent_id is None` 时跳过目标判据），
   这正是 5.2 要求的“无效目标仍可改名/停用/撤销/解除关联”；专用动词反而要为这些场景准备一条绕行。
4. **不可越界的能力已经由既有代码保证**：`scope` / `owner_user_id` 只从库里读，请求里无从指定；
   `display_name=None` 保留原名；`credentials=None` 不写凭据、不追加 `credential_versions`。

因此本阶段只做两处收紧（都在 `auth/service.py::update_tenant_channel_instance`），并在投影注释里写明
`repair_target` 指向的是哪条调用：

- 个人行的**命名空目标**一律拒绝（`personal_agent_required` 400），无论库中是不是本来就空；
  缺字段（`agent_id is None`）仍然是“保持原有目标”，这是改名得以通过的那条路。
- `allow_owner=True` 时预检改用 `_require_personal_instance_agent`，让个人面的拒绝理由只有一套。

`channel/web/web_channel.py` **未保留任何改动**（曾试加 `repair_target` 派发，已撤回并按 `git diff` 确认无残留）。

## C. 最终证据（改动后）

新增测试：`tests/test_personal_instance_target_repair.py`（28 个用例 + 21 个 subTest）。

| 要求 | 测试 |
| --- | --- |
| 5.1 无效目标仍可见并标记待修复（7 种形态） | `UnusableTargetVisibilityTests::test_every_unusable_shape_is_visible_and_flagged_for_repair` |
| 5.1 非本人目标不回读名称 | `…::test_a_foreign_target_is_never_read_back` |
| 5.1 修复只改目标（id/scope/owner/type/name/active 不变） | `RepairOperationTests::test_a_repair_changes_the_target_and_nothing_else` |
| 5.1 修复不能搬移归属（他人、平台管理员） | `…::test_a_repair_cannot_move_ownership`、`…::test_a_repair_cannot_take_over_a_public_instance` |
| 5.1 不替换默认/公共目标；空目标拒绝 | `…::test_a_repair_never_substitutes_the_default_or_a_public_agent`、`…::test_a_repair_without_a_target_is_a_malformed_write`、`…::test_an_empty_target_is_refused_even_when_the_row_is_healthy`、`…::test_the_operator_door_cannot_empty_a_personal_target_either`、`…::test_the_operator_door_can_still_rename_a_personal_row` |
| 5.1 拒绝一切非法新目标（他人私有/跨租户/已删/共享/停用），且不回显目标 | `…::test_a_repair_refuses_every_illegal_target` |
| 5.1 修复对“活的”目标重新判定，陈旧版本拒绝 | `…::test_a_repair_is_re_decided_against_the_live_target`、`…::test_a_stale_version_repair_is_refused` |
| 5.1 无效目标行禁止凭据轮换（且不留下凭据/版本） | `ClosingVerbsOnAnUnusableRowTests::test_a_broken_row_cannot_rotate_its_credentials` |
| 5.1 凭据与版本历史不被删改 | `…::test_a_repair_keeps_the_credential_and_its_version_history` |
| 5.2 无效目标行仍可改名/停用/撤销/解除关联 | `ClosingVerbsOnAnUnusableRowTests`（4 个用例） |
| 5.2 修复不自动、健康行不被改写 | `…::test_reading_or_listing_never_rewrites_a_row` |
| 5.2 公共路径回归（空目标仍走租户默认；公共实例可解绑；公共管理资格不变；两面互不串行） | `PublicPathRegressionTests`（5 个用例） |
| 5.2 公共路径的端到端起正控制（既有用例，未改动） | `tests/test_tenant_channel_inbound_closure.py::test_an_agent_less_instance_runs_with_the_tenants_own_default` |
| 5.1 `actions` 承诺的动作在 HTTP 上真能调用（`action="update"`） | `RepairActionOverHttpTests`（3 个用例，走真实 WSGI app） |

命令与结果见报告正文 D 节。
