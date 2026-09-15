# Stage 9.3 — 回归与路由基线

任务：完成双租户多用户完整授权回归、现有公共渠道/目录回归及新增方法级路由基线检查；
不以模拟界面证明生产能力已开放。

## 1. 双租户多用户完整授权回归

新增 `tests/test_personal_console_multi_tenant_authorization.py`（16 项，全部通过）。
既有验收矩阵是**单租户**内的成员/管理员切分；本文件补上交付时才成立的那一半：
**两个租户、多个用户、同一个身份库**。

夹具：tenant A（`acme`，平台管理员 `root`）+ tenant B（`beta`，走普通
`create_tenant(admin_username=...)` 路径，因此其管理员是真正的租户管理员而非第二个平台账号）；
成员 A：`alice`/`bob`，成员 B：`carol`。

| 用例 | 断言 |
| --- | --- |
| `test_the_other_tenants_member_sees_nothing` | B 成员的个人渠道列表为空（服务层与 `/api/personal/channels` 双查） |
| `test_a_foreign_instance_id_is_not_an_authorization` | 跨租户拿 A 的实例 id 走 `update`：HTTP 非成功、服务层抛错、owner 行逐字段未变 |
| `test_a_forged_tenant_header_does_not_reach_another_tenants_rows` | B 成员伪造 `X-Tenant-ID=A` 不得读到 A 的行（租户来自已验证上下文，不来自请求头） |
| `test_the_platform_admin_cannot_read_the_member_configuration` | 平台管理员治理投影含 id/channel_type/owner，**逐字段**不含 display_name/agent/credentials/parameters；个人 API 对其仍按 owner 过滤（列表为空） |
| `test_the_other_tenants_admin_is_refused_governance_and_configuration` | B 管理员对 A 的实例：同租户 id 403、跨租户 403/404；治理状态未被改动 |
| `test_the_platform_admin_can_still_govern_the_instance` | 治理停用可用且阻住 owner 自行重启（治理优先） |
| `test_only_the_owner_passes_the_resource_check` | `check_resource_action` 六元矩阵：owner True；同租户同侪、跨租户成员、跨租户成员伪造租户头、跨租户管理员、平台管理员全 False（`read` 与 `edit` 各判一次） |
| `test_the_other_tenants_projection_never_carries_the_object` | B 成员的 `view=personal` 投影为空；换 A 的租户头被拒 |
| `test_a_delete_from_another_tenant_leaves_the_object_in_place` | 跨租户删除被拒且绑定仍在 |
| `test_agent_management_actions_stay_owner_scoped_across_tenants` | 矩阵 + wire：owner 自己的对象 True，他人对象（同租户/跨租户/跨租户管理员）False；`/api/agents` `update` 对他人物件 403 且错误体非成功 |
| `test_memory_written_in_one_tenant_is_absent_in_the_other` | A 写入的 `memory/notes.md` 在 B 的成员视图下列表为空、同名条目读到空内容；A 本人读到原文 |
| `test_the_same_account_id_in_another_tenant_gets_its_own_root` | 纵深防御：同一 user id 在另一租户解析到另一个根，读不到对方内容 |
| `test_the_other_tenants_admin_gets_no_memory_surface` | B 管理员走 `/api/memory/personal` 拿不到 A 的内容 |
| `test_the_member_still_cannot_reach_the_tenant_channel_api` | 成员对 `/api/channels`（platform）与 `/api/tenant/channels`（tenant+tenant_admin）均非 200 |
| `test_each_tenant_admin_sees_only_their_own_tenant_channels` | B 管理员看到 B 的目录（成功），换 A 的租户头被拒且响应不含 `acme`；A 成员的私有实例不在其中 |
| `test_a_personal_instance_does_not_appear_in_the_tenant_catalogue` | 个人行与租户行是两个目录，个人实例不进入运营者列表 |

变异检查（改坏实现，看用例是否抓到）：

| 变异 | 结果 |
| --- | --- |
| M1 `list_personal_channel_instances` 去掉 `owner_user_id=?` 过滤 | **被捕获**（`test_the_platform_admin_cannot_read_the_member_configuration` 看到他人私有行） |
| M2 把 `check_resource_action` 的私有归属短路移到 `all` 旁路之后 | **被捕获**（平台管理员被判为可读他人私有 Agent） |

## 2. 现有公共渠道/目录回归

| 批次 | 命令要点 | 结果 |
| --- | --- | --- |
| 公共渠道（11 文件） | `test_tenant_channel_instances_service/migration`、`test_tenant_channel_mutations`、`test_tenant_channel_console_scope`、`test_tenant_channel_http`、入站三件套（anchor/closure/isolation）、`credential_landing`、`required_credentials`、`isolation_acceptance` | **181 passed + 7 subtests** |
| 智能体/目录/授权（12 文件） | `test_agent_admin`、`test_agent_web_management`、`test_tenant_default_agent(_selection)`、`test_tenant_agent_provisioning`、`test_identity_policy`、`test_identity_resource_authorization`、`test_rbac_execution_permission`、`test_rbac_tenant_constraints`、`test_web_chat_boundary`、`test_user_personal_agent_provisioning`、`test_management_personalize_personal_agents` | **246 passed** |
| 前端 `.cjs` 逐文件（35 文件） | `node --test <file>`，与 HEAD worktree 的同一扫描逐行对比 | 仅 `test_sidebar_account_frontend.cjs` 的 **5 项在 HEAD 上即失败**（`mode: 'legacy'/'unknown'` 断言早于 legacy 身份模式退役），本 change 未新增失败；`test_console_i18n_parity.cjs` 由 4/5 变 5/5（本 change 修好），`test_console_view_registry.cjs` 恢复 6/6 |
| 真实浏览器契约 | `NODE_PATH=…/node_modules node tests/test_personal_console_browser.cjs` | **11 scenarios passed**，`pageErrors: []`，`unexpectedRoutes: []`，请求日志显示被拒页面不请求消费者 |

> 说明：`pytest` 一次性收集全仓会因 `scenes/config.py` 与顶层 `config.py` 同名冲突而大面积
> 收集失败，这是**基线既有**状况（HEAD worktree 实测 104 个收集错误，本工作树 131 个 = 104 +
> 本 change 新增的 27 个测试文件），并非本 change 引入；上述每个批次与每个新增文件单独运行
> 均为全绿。5 个 sidebar 前端用例同理，用 HEAD worktree 的逐文件扫描证明失败集合完全一致。

## 3. 新增方法级路由基线检查

`scripts/route-baseline.txt` 追加 `enable-member-personal-console` 段（9 行方法级条目）：

```
/api/memory/personal	GET	personal	-
/api/memory/personal	POST	personal	-
/api/memory/personal/content	GET	personal	-
/api/personal/channels	GET	personal	-
/api/personal/channels	POST	personal	-
/api/personal/channels/([^/]+)	GET	personal	-
/api/personal/channels/([^/]+)	POST	personal	-
/api/personal/resources	GET	personal	-
/api/personal/resources	POST	personal	-
```

`tests/test_route_registry.py` 新增 `PersonalConsoleRouteTests`（4 项）：

1. `test_every_personal_route_is_session_scoped_and_carries_no_permission` —— 九条路由全部为
   `personal` 且**不带**路由级权限（同一路径服务多个不同权限的操作，权限在 handler 内判），
   并显式断言它们不是 `public`/`tenant`/`platform`（前者会跳过会话，后者会让浏览器直接拿不到）。
2. `test_the_frozen_baseline_records_exactly_these_methods` —— 冻结基线里逐条为
   `("personal", "")`，方法集合被钉住（多一个/少一个方法都会失败）。
3. `test_the_management_surfaces_keep_their_administrator_policies` ——
   `/api/channels` 仍 `platform`、`/api/tenant/channels` 仍 `tenant`、`/api/agents` 仍
   `tenant`，且都不得变成 `personal`：个人面是新增，不是把管理面放宽。
4. `test_a_session_is_required_before_the_handler_runs` —— 走真实 `http_policy._match_policy`
   复核每条路由解析到 `personal`（不是只看注册字面量）。

变异检查：把 `/api/personal/resources` GET 从 `personal` 改成 `tenant` → **3 项失败**
（含既有的 `FrozenBaselineEquivalenceTests.test_derived_policy_matches_frozen_baseline`），
说明冻结基线与新增 pin 都在真实生效。

## 4. 「不以模拟界面证明生产能力已开放」

- 浏览器契约跑的是**生产页面**（真实 `chat.html` + `console.js` + 模块 + CSS + 服务端投影
  夹具），不是另写的模拟 UI；上面第 2 节的浏览器结果即它在本 change 代码下的输出。
- 反过来，它也不被用作「已开放生产能力」的证据：个人执行仍由
  `PERSONAL_RUNTIME_ACCEPTED_TYPES = frozenset()` 与 `personal_channel_runtime=False` 关死，
  浏览器契约只证明**被拒页面不启动消费者**与目录/拒绝文案正确。
