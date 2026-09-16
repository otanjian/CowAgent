# 6.1 共用渠道面：同一页面、同一业务接口、按对象范围区分角色

本文件记录 `tasks.md` 6.1：让「本人连接」和「租户公共连接」复用**同一个**
消息渠道页面和**同一个**租户业务接口，不再有「个人工作台 / 租户工作台」两套。

## 1. 一个接口，两个范围

`/api/tenant/channels` 及其子路径不再是「租户管理员专用」。是否放行由**对象范围**
决定，且该判定只有一处：`auth/object_scope.allows_channel_instance`。

| 调用者 | 列举范围 |
| --- | --- |
| 普通成员 | `tenant=T AND scope='user' AND owner=U` |
| 租户管理员 | 上者 **并** `tenant=T AND scope='tenant'` |
| 平台管理员 | 本页只答本租户；实例级面仍是 `/api/channels`（平台域） |

`IdentityService.list_tenant_channel_instances` 由「`_is_control` 否则 403」改为
按范围取行（`auth/service.py:8174`）。两处刻意的选择：

* **管理员看得见自己的本人连接**——管理员也是 owner，把管理员当成「只应看公共连接」
  的角色会让他在自己配了一条本人连接后找不到它；
* **管理员看不见同事的本人连接**——不是过滤掉，而是 SQL 根本不选中，因此没有
  任何请求字段能把它捞出来；
* **无有效成员资格 → 拒绝**，而不是空列表：「你不能列举」和「这里没东西」是两件
  事实，合并会把授权缺口报成空租户。

列表投影沿用既有脱敏（`_instance_projection`，不含 `credentials`/密文/任何值）。

## 2. 范围由目标派生，不由请求声明

摘要见 `evidence/6-2-channel-target-derivation.md`（该任务把归属派生与目标候选一并收口）：
请求体里的 `scope` / `owner_user_id` 不被读取，归属由服务端按
`IdentityService.channel_target_scope()` 从目标绑定事实派生。

## 3. 编辑与启停按行复判，而不按门

`_require_channel_instance_in_range`（`auth/service.py:8232`）在**写锁内**用行自身的
`scope`/`owner` 复判，两条分支都走它：

* 本人分支先证所有权，再复判（他人行 → 403）；
* 管理分支也复判——否则「可编辑列表」会变成编辑同事私有连接的入口。

因此`set_tenant_channel_instance_active` 里那句 `allow_owner and ...` 之外的老路径
被补上：**管理员不能启停他人本人连接**。治理停机仍是独立治理面（`governance_disabled`），
不混入可编辑列表——这正是 `tenant-channel-configuration` 规范要求的分离。

## 4. 页面投影与接口一致

`admin.channels` 对成员报 `scope: "self"`、`available: true`
（`auth/service.py:3472`），不再是 `no_tenant_control`。理由不是「放宽」：租户接口
**确实**会答这个调用者，报页面关闭会在一个能用的面前面挂一把锁——正是本 change
要消除的缺陷形状（及其镜像：页面承诺、接口拒绝）。

成员的本人面还带上 `switches` 与 `states: {read, config, execution}`，是被退役的
`personal.channels` 那三项的**平移**（task 8.1）。它不是装饰：「目录可读、执行未开」
是一个真实状态——成员可以在任何厂商运行被证明之前先配好连接——`execution` 与
`config` 分开正是为了让页面能说出这件事，而不是要么藏起可用的表单、要么承诺一条
已上线渠道。

## 4.1 共用列举揭开的一处错误代理

回归扫描（`pytest tests/ -k "channel or scan"`）抓到 11 个扫码用例失败，根因不在扫码
本身，而在它依赖的一个判断被 6.1 改动了含义：

`web_channel._manages_tenant_channels(ctx)` 曾用「能不能列举租户渠道」作为「有没有
管理资格」的代理。这在 6.1 之前**恰好**成立——那时普通成员列举直接 403。共用之后
不再成立：成员现在也能列举（列举到的是本人连接），于是被判成管理者，扫码给本次接入
派一个 `tenant` 作用域，而该作用域在写入时必然被拒
（`channel instance manage denied`）。这正是本 change 反复要消掉的形状：**先给人一个
随后必被拒的位置**。

改为直接问治理资格本身（身份投影里的 `is_tenant_admin` / `is_platform_admin`，即租户
业务接口写入路径真正使用的那个事实）。`tests/test_weixin_qr_flow.py` 40 passed。

教训记在这里：一个「用相邻接口的成败当权限代理」的判断，会在那个接口放宽时静默反转
语义。共用接口尤其如此——同一个 200 对两个角色意味着不同的范围。

另一个回归同样来自旧契约：`test_personal_console_multi_tenant_authorization.py::
PublicChannelSurfaceRegressionTests::test_the_member_still_cannot_reach_the_tenant_channel_api`
断言的正是「成员不得触达租户渠道接口」。按新契约改写为
`test_the_member_reaches_the_shared_api_scoped_to_their_own_range`：平台目录
`/api/channels` 仍守平台门；租户目录成员可达且答 `scope: "self"`、不含他人连接；
以非本人目标创建仍被拒。

## 5. 前端：一个页面，按范围收窄可选内容

`channelScope()` 拆为 `channelPageScope()`（`self` | `tenant` | `platform`，用于文案）
与 `channelScope()`（落到哪个加载器）。`self` 与 `tenant` 加载**同一个**
`loadTenantChannelsView()`，只是服务端答的范围不同。

本人范围下表单收窄（`tenantChannelSelfScope`）：

* 类型目录只列 `ready` 的声明（`tenantChannelTypeChoices`）；
* 目标选择只列本人、启用、可用的私有智能体，且**不提供空目标**
  （`tenantChannelAgentOptions`）——空目标在本人创建时必被拒，摆出一个点不成的选项
  就是本 change 要消掉的「可点但被拒」形状。

管理范围保持原样：类型全列，空目标保留（租户公共连接可以刻意不绑定）。

## 6. 证据

```
.venv/bin/python -m pytest tests/test_tenant_channel_member_access.py -q -p no:randomly
→ 20 passed
```

`tests/test_tenant_channel_member_access.py`（新）在处理器边界钉住契约：

* `MemberListingTests`——成员只列本人、管理员列公共+本人且**不含**同事私有、
  列表不含任何凭据字段或值、成员拿到的是本人契约的类型目录（带 `ready`）；
* `MemberCreateTests`——成员创建落在 `scope='user'` 且 owner 为自己；请求要
  `tenant` 也仍是本人；管理员缺省建公共、显式私有目标建本人；成员路由到共享
  Agent 被拒；同事永不被代填为 owner；
* `MemberWriteTests`——owner 可编辑/启停自己的；不能编辑同事的、不能启停公共的；
  管理员可启停公共的、**不能**启停成员的；
* `ChannelsPageProjectionTests`——成员页面 `available/read_allowed`、`scope='self'`、
  `actions.create/update`；管理员仍 `tenant`；平台管理员仍 `platform`；
  页面跟随所选租户的成员资格。

## 6.1.1 变更检测：范围谓词被变异测试钉住

`tests/test_tenant_channel_mutations.py` 原来按字面钉住旧列举 SQL。6.1 改了查询，
两个锚点随之改为新范围谓词，并**新增第 5 条变异**（去掉成员分支的
`owner_user_id=?`，代以恒真的 `? IS NOT NULL`，保持占位符数量以让变异抵达断言而
不是死在绑定错误上）：它必须被
`MemberListingTests::test_a_colleagues_connection_never_appears_for_this_member` 抓到。
该用例本身是本任务新增的——旧列举查询对成员直接 403，没有「本人范围」可越界，
因此也没有对应的钉。

第一版选的钉子是 `test_a_member_lists_only_their_own_connections`，变异存活了：该
用例的同租户里只有成员自己一条 `scope='user'` 行，仅按 kind 过滤与按 owner 过滤
结果相同。换成「同租户另有一位成员的连接」后才真正钉住——这正是「看起来在验证
隔离、实际什么都没验证」的形状，记在这里以免下次再选错。

同时修掉该 harness 的一处固有抖动：它在写回变异后立刻起子进程，而 CPython 用
「源码大小 + mtime」校验 `.pyc`，粗粒度时间戳下子进程会导入**未变异**的字节码，
把已被抓住的变异报成存活。现在 `_run` 会清掉项目自身的 `__pycache__` 并设
`PYTHONDONTWRITEBYTECODE`（跳过 `.venv`/`.git`/`node_modules`）。连跑三次稳定：
`1 passed, 5 subtests passed`。

改写的旧契约用例（原断言「成员被 403」）：

* `tests/test_tenant_channel_http.py`——`test_a_plain_member_is_denied` 拆为
  `..._lists_only_their_own_range` 与 `..._cannot_create_a_public_connection`；
* `tests/test_tenant_channel_console_scope.py`——成员页面断言由「无管理」改为
  「同一页面的本人范围」；
* `tests/test_personal_channel_console.py`——`..._keeps_its_administrator_gate` 保留
  创建/启停的 403，列表改为按范围返回；
* `tests/test_capability_matrix.py`——`admin.channels` 对 bare member 由不可用改为
  本人范围可用（内存页的「逐请求范围」断言不变）。

前端：

```
node --test tests/test_channel_scope_nav_frontend.cjs tests/test_tenant_channel_frontend.cjs \
  tests/test_tenant_channel_card_frontend.cjs tests/test_channels_page_header_frontend.cjs \
  tests/test_tenant_default_agent_frontend.cjs
→ 全部通过
```

变更检测：`tests/test_tenant_channel_mutations.py`（5 条变异，连跑三次稳定通过）
——见 §6.1.1。

新增/更新断言覆盖：成员入口可见且映射到业务面（`self` → 租户加载器，绝不是实例级
`/api/channels`）；`self` 文案键 `tenant_channel_self_desc`（三语 + i18n 快照）；
本人范围类型目录只列 `ready`；卡表单桩新增 `tenantChannelSelfScope`。

## 7. 未纳入本任务

* `/api/channels`（实例级全局面）保持平台域，未改其守卫；
* 段 6.2–6.6（表单字段与归属派生的一致性收紧、启停与运行反馈、真实提供方运行）
  未在本任务内完成；
* 旧个人接口路径本身（`/api/personal/channels`）仍在，仅作为共用服务的适配层，
  其退役在段 6 后续任务。

## 8. 收尾补齐：`create` 随「可配置面」而非「可读面」（并被一次并发写回覆盖后重新落盘）

§5 记录成员入口为 `scope='self'`、`actions.create/update` 均为真。收尾复核 3.2 子代理提出的
缺口时发现：当**没有任何渠道类型就绪**（`states.config=false`）时，投影仍给
`actions.create=true`，而成员类型的候选集合此时为空 —— 前端会渲染一个点进去没有可选项的
表单，写入路径随后拒绝。这正是本 change 反复要消除的「可点不可用」形状（与停用对象
`can_chat=false` 同一类）。

落盘改动（`auth/service.py` 的 `admin.channels` 分支）：

* `ready` 在构造 `entry` 之前按 `scope=='self'` 求值；
* `actions.create = bool(allowed and (ready is None or bool(ready)))` —— 管理员
  （`scope` 为 `platform`/`tenant`，`ready is None`）不受成员类型目录影响，
  因为那目录不是他们的可接入面；
* `states.config`/`states.execution` 继续按 `ready` 与运行开关报告，语义不变。

回归固定：`tests/test_personal_console_pages.py::OfferedActionAgreesWithTheSurfaceTests`
（5 条，含负向对照）。变异验证（把 `bool(allowed and (ready is None or bool(ready)))`
改回 `allowed`）：`test_an_empty_channel_catalogue_withdraws_create_not_the_page` **失败**，
还原后通过。

### 并发写回事故与恢复

本次改动一度**被并发写回覆盖**：任务 4.7 的验证子代理在负向对照中把 `auth/service.py`
还原为较早副本，其“与对照前哈希一致”的自检以**已被覆盖的状态**为基准，因此没有发现它同时
清掉了本节与 `admin.agents.actions.create` 的收紧。两者已在收尾时重新落盘，并以新增测试
固定（见本节与 `evidence/2-2-qualification-split.md` §6）；`auth/service.py` 中本轮
新增的 `canonical_menu_id`/`LEGACY_PERSONAL_MENU_MAP` 导入亦经 `rg` 逐项复核仍在。
教训已落实到方法：并发写入期间以“断言/守卫是否仍在”复核，而不是只比对文件哈希。

```
$ .venv/bin/python -m pytest tests/test_personal_console_pages.py -q -p no:randomly
25 passed
```

### 复核为非缺口：`admin.channels.actions` 不含 `enable`

3.2 子代理提出旧 `personal.channels` 的动词表含 `enable`、共用页只有 `create`/`update`。
复核结论是**非缺口**：启停按钮按**行自身**状态渲染
（`console.js:14497` 由 `inst.active` 决定调用 `toggleTenantChannel(id, true|false)`，
`:15004` 提交 `{active, expected_version, recent_password}` → `TenantChannelActiveHandler`），
前端从不读页面级 `actions.enable`（全仓 `rg "actions.enable"` 无命中）。补一个无人读取的动词
只会在投影里制造第二份真相，因此不改；成员对本人的行与管理员对本人的行都经
`allow_owner` 路径启停，他人本人连接一律拒绝（见 `test_tenant_channel_member_access.py`）。
