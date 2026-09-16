# 8.2 一个兼容发布周期：调用观测与旧入口收口

对应 `tasks.md` 8.2：「记录一个兼容发布周期及调用观测，确认正式页面不再使用旧入口；
按迁移报告收口旧菜单、开关与个人专属组件，退役不删除业务数据或凭据。」

本文件回答四个可以被证伪的问题，每个都落到 `file:line` 或可重复执行的断言上：

1. 旧入口里**还有哪些活着**（清单 + 位置）；
2. 正式页面**是否还在调用**它们（调用观测）；
3. 旧开关**谁在读**（台账）；
4. 这一周期**删了什么、没删什么**（退役不等于删数据）。

## 0. 观测快照

观测时间 **2026-09-16 02:38 CST**（`HEAD = cbdb9884`，工作区含各并行 worker 的未提交改动）。
引用文件的 sha256 前缀（**行号以本快照为准**）：

| 文件 | sha256 前缀 |
| --- | --- |
| `channel/web/static/js/console.js` | `39884b25c6f1440d` |
| `channel/web/static/js/personal-console.js` | `6412295ae41981b2` |
| `channel/web/static/js/i18n/personal-console.js` | `704a876f4088b6af` |
| `channel/web/chat.html` | `139f566a481726b2` |
| `channel/web/route_registry.py` | `d14f7d63d1a6cc83` |
| `auth/service.py` | `4256158829953995` |
| `auth/policy.py` | `7328981c17450eb6` |
| `auth/store.py` | `5db362050c3ee4ca` |
| `agent/private_agent.py` | `d92fa3e056eb4726` |
| `agent/memory/personal.py` | `91a9f8917d512046` |
| `channel/channel_instances.py` | `82ab0e315fe03ed4` |
| `channel/weixin_scan_adapter.py` | `b62d76dacaaa148e` |
| `config.py` | `a26eeee908a90c4f` |
| `scripts/route-baseline.txt` | `55115b9e1a59279e` |

本周期内**另一路工作正在同一批文件上收口**，快照之间发生了两次可观测变化，都已按本节
口径重新核对并写进下文（不是把旧结论沿用下来）：

| 时间 | 变化 | 影响 |
| --- | --- | --- |
| 首轮快照 | 链尾新增 `_migration_27`（`auth/store.py:1431`）；`agent/private_agent.py` 新增一处 `member_personal_console` 读取（`:209`） | §3 台账与 §4 的「删除集合」判据按新事实改写 |
| 二轮快照（本快照） | 任务 5.5 落地：`PERSONAL_VIEWS` 由 5 项减为 3 项、`/api/personal/resources` 从路由表移除并标 `REMOVED`、个人参数改由 `/api/tools`/`/api/skills` 承载；`auth/service.py` 的 `admin.models` 页 scope 由 `platform` 改为 `tenant` 并新增 `model_catalog_open()` | §1.1 视图清点由 5 改 3；§1.2 新增「个人资源面已移除」；8.5 的分项判定随之更新 |

**引用行号以本快照为准**；这些文件的判定依据是 `tests/test_compat_surface_closure.py` 的断言
（读当时的文件、每次重算），而不是某一行的位置——行号变了就按断言结果读，不要按行号复述结论。

### 观测口径

* **静态清点**：直接读代码，不看注释里的自述（本文件里所有「已收口」都来自读代码或
  跑断言，注释只用来解释*为什么*）。
* **调用观测**：新增 `tests/test_compat_surface_closure.py`（11 项）把调用面钉成断言——
  旧端点只允许退役组件调用、**已移除的资源端点与视图必须是零出现**、旧视图只允许退役组件注册、
  正式模块只能经两个生命周期钩子接触退役组件、开关读取方是一张台账。**它不是一次性的报告，
  而是一个每次跑都会重算的观测器**：调用面变化会让它失败。
* **未做**：生产流量计数（见 §7）。

## 1. 旧入口清点

### 1.1 仍活着（代码层面）

| 旧入口 | 位置 | 形态 | 判定 |
| --- | --- | --- | --- |
| 五个旧视图 id | `console.js:1462-1466`（`VIEW_META` 的五个 personal 项） | 只作**转接表**：`LEGACY_PERSONAL_FORWARD`（`1802-1808`）与 `legacyPersonalForward`（`1813-1816`）经 `VIEW_META` 解析目标 | 保留（受权转接的落地） |
| 退役组件本体 | `personal-console.js:70-82`（`PERSONAL_VIEWS`，**剩 3 项** agents/channels/memory）、`771-793`（`window.PersonalConsole`）、`799-805`（注册这 3 个视图） | 仍在发行：`chat.html:2821` 加载 | **未收口**（`personal-tools` / `personal-skills` 已随任务 5.5 退役，见 §1.2） |
| 旧个人 i18n 命名空间 | `i18n/personal-console.js`（三语各 116 键，共 348 条文案，391 行）；仍由 `chat.html:2803` 加载 | 其中 `nav_group_personal` 与 5 个 `menu_personal_*`（三语共 6 个键名）被 `console.js` 的 `VIEW_META`（`1462-1466`）引用，其余只被退役组件引用 | **未收口**（随组件一起退役） |
| 旧个人端点 | `route_registry.py:200-203`（**4 条路由、7 个方法**，策略 `personal`，来源 `fork:member-personal-console`）；冻结基线 `route-baseline.txt:238-244` | 薄适配，委托统一服务（见 `evidence/8-1-*.md` §3） | 保留（兼容期数据面） |
| 旧菜单 grant 词汇 | `auth/service.py:135`（`canonical_menu_id`），唯一读取点 `auth/service.py:3373` | 投影读授权时把 `nav:personal.*` 归一化为正式页 id | 保留（迁移后写入的旧 grant 仍能被正确理解） |
| 五个能力开关 | `auth/policy.py:564-570`（名册）、`577-583`（出厂值）、`589-598`（页面依赖 `PERSONAL_PAGE_CAPABILITIES`）、`617-632`（唯一读取入口 `personal_capability_enabled`） | 仍被读取，见 §3 | **未收口**（前置见 §5） |
| 投影里的 `personal.*` 页面 | `auth/service.py:117-121`（`_SIGNED_CONSOLE_PAGES` 的五项）、`173-187`（`_PERSONAL_PAGE_META`）、`3451` 起（逐页签名循环） | 仍向 `/auth/context` 下发 | **未收口**（其中 `personal.tools` / `personal.skills` 的视图已退役、页面仍被签发，属 §1.2 未清干净的一半） |

### 1.2 已收口

| 旧入口 | 收口方式 | 证据 |
| --- | --- | --- |
| 存量角色的旧个人菜单 grant | `_migration_26` 事务化幂等映射（`auth/store.py:1348-1428`） | `evidence/2-4-menu-mapping-migration.md`；本 change 新增演练 `tests/test_console_migration_drill.py` |
| 内置角色的默认授权 | `BUILTIN_MENU_DEFAULTS`（`auth/policy.py:515-536`）不再含任何 `personal.*` | `tests/test_console_menu_mapping.py:179` |
| 账号菜单五项个人入口 | 已删除 | `evidence/3-3-account-menu-resources-removed.md`；`tests/test_account_menu_no_personal_resources.cjs` |
| 侧栏「我的」分组 | `chat.html` 里 22 个 `id="view-*"` 容器**没有一个是 personal 视图**（`rg 'id="view-personal' channel/web/chat.html` 计数 0） | 同上 |
| **个人资源面（任务 5.5）** | `personal-tools` / `personal-skills` 两个视图不再被任何模块注册（旧地址仍经 `LEGACY_PERSONAL_FORWARD` 转发）；`/api/personal/resources` 从 `route_registry.py` 移除、冻结基线标 `REMOVED`（`route-baseline.txt:245-246`），个人参数改由 `/api/tools`、`/api/skills` 承载；账号命名空间里 `account_menu_resources`（+ `_checking`/`_failed`/`_retry`）与 `account_menu_region_personal` 五个旧键已从三语删除，`account_menu_trigger_hint` 由「个人资源与设置」改为「账号设置」 | `tests/test_compat_surface_closure.py::test_the_removed_resource_endpoint_has_no_caller_and_no_registration`（零出现断言，扫前端 + 生产 Python）；前端用例 70 passed 仍全绿 |
| 直达旧地址 | 受权转接，且转接在可用性门禁与离页检查**之前** | `evidence/8-1-*.md` §2；`tests/test_personal_address_forward_frontend.cjs` |
| 无效的默认指针 | `_migration_25` 清理指针、保留 `private_owner_user_id`、逐条审计（`auth/store.py:1233-1345`） | `evidence/2-3-user-default-migration.md` |
| 原生 Desktop | `git ls-files desktop` 共 117 个受版本控制文件（其中 `desktop/src/**` 96 个）无任何旧个人令牌 | `rg 'personal-console\|PersonalConsole\|nav:personal' desktop/ --glob '!dist/**'` 命中 0 |

## 2. 调用观测：正式页面是否还在用旧入口

静态清点 + `tests/test_compat_surface_closure.py` 的观测结果：

    39|```
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_compat_surface_closure.py -q -p no:randomly
→ 11 passed
```

| 观测项 | 结果 |
| --- | --- |
| 旧个人端点（`/api/personal/channels`、`/api/memory/personal`、`/api/agents?view=personal`）的正式调用方 | **0 个**。全部命中都在 `personal-console.js`（`74`、`77`、`80`），反向断言同一用例确认端点确实在该文件里（避免「零调用」是搜索写错） |
| **已移除**的个人资源端点 `/api/personal/resources` 的调用方与注册 | **0 个**（前端全文 + `auth/`、`agent/`、`channel/`、`cli/` 的生产 Python）。判据是零出现，不是「只准退役组件调用」——留一条只被旧组件调用的路由等于继续养着被取代的第二个写入口 |
| 注册旧 `personal-*` 视图的正式模块 | **0 个**。退役组件注册 **3 个**（`id: 'personal-agents' / 'personal-channels' / 'personal-memory'`），其余模块没有 |
| 已退役的 `personal-tools` / `personal-skills` 视图 | **零注册**（前端全文按 `id: '...'` 形态扫描）；同时反向断言旧地址 `'personal-tools':` / `'personal-skills':` 仍在 `LEGACY_PERSONAL_FORWARD` 里可转发 |
| 正式模块接触退役组件的方式 | 只有 2 个生命周期钩子：迟到响应失效 `PersonalConsole.invalidatePersonalViews()`（`console.js:1833-1837`、`identity-admin.js:35-37`）与离页草稿守卫 `window.__personalConsoleDirtyGuard__()`（`console.js:1988-1989`）；`loadPersonalView` / `PERSONAL_VIEWS` / `personalFormFields` 等数据面入口在正式模块里**零引用** |
| 旧 i18n 命名空间的正式引用 | 只有 `console.js` 的 `VIEW_META`（6 个键名：`nav_group_personal` + 5 个 `menu_personal_*`，三语共 18 条）；其余只在退役组件内 |

**结论（与 8.1 §5 的交接一致）**：数据面已经收口——正式页面不再调用、不再渲染
旧个人端点与视图；**组件依赖尚未收口**：两个正式模块仍调用退役组件的失效钩子，且
`chat.html:2803` / `:2821` 仍加载 i18n 与组件本体。所以任务 8.2 的「确认正式页面不再使用
旧入口」只有**数据面**成立，**组件面**不成立，属于未收口，理由与前置见 §5。

### 观测器如何失败（变异验证）

观测器只有在会失败时才有价值，故在仓库副本（`/tmp`，未改工作区）里做了三次变异：

| 变异 | 预期失败用例 |
| --- | --- |
| 在 `console.js` 末尾加 `fetch("/api/memory/personal")` | `test_only_the_retired_component_calls_a_retired_endpoint` |
| 在 `agent/private_agent.py` 末尾加一处 `personal_capability_enabled("personal_memory_write")` | `test_every_capability_switch_has_exactly_the_recorded_readers` |
| 在 `console.js` 末尾加一句 `registerConsoleView({ id: 'personal-tools', … })` | `test_the_removed_views_are_registered_by_nobody`（`:219` 断言） |
| 在 `route_registry.py` 末尾加回 `RouteEntry("/api/personal/resources", …)` | `test_the_removed_resource_endpoint_has_no_caller_and_no_registration`（`:182` 断言） |

前两次当时本文件只有 9 项，失败形态是 `2 failed, 7 passed`；后两次在沙箱 `/tmp/mut38`
（`auth`/`agent`/`channel`/`cli`/`config.py` + 本测试文件的副本，**未改工作区**）里跑：
沙箱基线 `11 passed` → 变异 `1 failed, 10 passed` → 撤销变异 `11 passed`。

**这个观测器按设计失败过一次，且已按规则处理**：观测期间另一路工作给
`agent/private_agent.py` 加了一处 `member_personal_console` 读取（私有智能体创建前的
廉价拒绝，`agent/private_agent.py:209`），台账随即失败，随后**台账与本节 §3 一起更新**。
这正是台账存在的目的——读取方变化必须显式落地，而不是悄悄漂移。

## 3. 开关：谁在读（台账）

读取入口只有一个：`auth/policy.py:617-632`（`personal_capability_enabled`，未知名字
一律 `False`，fail closed），`auth/service.py:1395-1410`（`require_personal_capability`）
与 `:1412-1414`（只读探测 `personal_capability_open`）是它的服务层包装。

| 开关 | 出厂值（`policy.py:577-583` / `config.py:290`） | 读取方（本快照行号） |
| --- | --- | --- |
| `member_personal_console` | `True` | `auth/service.py:1464`、`:5780`、`:5974`（只读探测）、`:7911`；`agent/private_agent.py:209` |
| `user_private_agent_management` | `True` | `auth/service.py:1456`（私有智能体配额绑定）、`agent/private_agent.py:205` |
| `personal_memory_write` | `True` | `agent/memory/personal.py:582` |
| `personal_channel_onboarding` | `True` | `auth/service.py:7912`、`:9297`（只读探测）、`:9391`、`:9422`、`:9455`、`:9602`；`channel/weixin_scan_adapter.py:316` |
| `personal_channel_runtime` | **`False`** | `channel/channel_instances.py:1379` |

（`auth/service.py` 的读取点是**方法内部**的位置，同一方法可能同时读两个开关，例如
`:7911-7912` 一起挡下「保存个人渠道配置」。台账粒度是**文件 × 开关**，上面列出的是文件内
的命中位置，方便复核；行号随并行改动漂移，判定以断言为准。）

这张台账由 `test_every_capability_switch_has_exactly_the_recorded_readers` 每次重算：新增
读取方会让它失败，必须连同本节与场景一起更新。

`console-information-architecture` 要求「迁移完成后 SHALL 移除旧开关的独立运行读取，以共用
状态为唯一依据」。**现在不成立**：五个开关仍各自被上表逐点读取，而不是由统一能力状态
供给；其前置是阶段 7 的运行改造（`personal_channel_runtime` 必须先有真实提供方验收，
7.4/7.6 阻塞于凭据）。因此本项记为**未收口**，不记为通过。

## 4. 退役不删除业务数据或凭据

本周期**没有任何数据被删除**，只有两类策略行被改：

* `_migration_25`（`auth/store.py:1233-1345`）：`ALTER TABLE` 加两列 + `UPDATE` 清空无法
  解析的默认指针，**保留** `private_owner_user_id`；链条里没有任何迁移给
  `private_owner_user_id` 赋值或重建 `agent_bindings`（静态护栏
  `test_no_migration_in_the_chain_assigns_a_private_owner`）。
* `_migration_26`（`auth/store.py:1348-1428`）：唯一的 `DELETE` 是
  `role_resource_grants` 里 `resource_kind='menu'` 的旧个人页授权行——**策略行**，管理员
  可以重建；业务对象（智能体绑定、成员、角色、非菜单授权）一行不动。

观测期间另一路工作在链尾新增了 `_migration_27`（`auth/store.py:1431-1477`）：它只**追加**
`nav:admin.models`（内置角色的新共用页），不删除任何行，故本节结论不变。新增的
`tests/test_console_migration_drill.py::test_the_upgrade_deletes_no_business_row` 因此断言
「被删除的集合 ⊆ 旧个人菜单授权行」，而不是「行数必须变小」——链条允许追加，删除只允许
落在退役的菜单授权上。

演练断言（`tests/test_console_migration_drill.py`）：

```
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest             \
  tests/test_user_default_migration.py tests/test_personal_instance_target_repair.py \
  tests/test_management_repair_tenant_defaults.py tests/test_console_menu_mapping.py \
  tests/test_console_migration_drill.py tests/test_compat_surface_closure.py \
  -q -p no:randomly
→ 110 passed, 23 subtests passed (0:01:22)
```

* `test_the_upgrade_deletes_no_business_row`：升级前后 `agent_bindings`/`users`/`tenants`/
  `memberships`/`roles` 行数不变、非菜单授权行数不变；被删除的行集合 ⊆ 旧个人菜单授权行
  （链条允许追加，故不写成「行数必须变小」）；
* `test_a_repair_keeps_the_private_owner_of_every_agent`：全部 `(agent_id,
  private_owner_user_id)` 对逐一不变；
* `test_rolling_the_data_back_cannot_reopen_the_retired_state`：把升级前的库盖回去再启动，
  收口会被**重新执行**而不是被接受为现状。

凭据与运行实例：迁移既不出行也不删行（`credentials`、`tenant_channel_instances` 在演练
夹具里为 0 行且升级后仍为 0 行，见
`test_the_chain_does_not_enable_a_capability_switch_or_a_runtime_shard`）。真实凭据明文/
密文在本周期内**没有任何读写路径经过迁移**。

## 5. 判定汇总

| 收口项 | 判定 | 依据 / 未收口的原因 |
| --- | --- | --- |
| 旧菜单 grant（存量） | ✅ 已收口 | `_migration_26` 幂等映射 + 8.3 演练 |
| 内置角色默认授权 | ✅ 已收口 | `policy.py:515-541`（`admin.models` 由 `_migration_27` 追加到内置角色，属新增共用页，不是旧个人页回流） |
| 账号菜单五项入口、侧栏分组、页面 DOM 容器 | ✅ 已收口 | `evidence/3-3-*.md`；`chat.html` 无 personal 容器 |
| 正式页面的旧入口**数据面**调用 | ✅ 已收口 | 新观测器 §2 |
| 个人资源面（`personal-tools` / `personal-skills` 视图、`/api/personal/resources`） | ✅ 已移除（任务 5.5） | §1.2、§2；零出现断言 + 变异验证 |
| 无效默认指针 + 归属保护 | ✅ 已收口 | `_migration_25` + 静态护栏 |
| 原生 Desktop | ✅ 无旧个人代码 | `desktop/`（117 个受版本控制文件，其中 `desktop/src/**` 96 个）命中 0（`dist/` 未纳入版本控制，不作依据） |
| 退役组件 `personal-console.js` 与旧 i18n 命名空间 | ❌ 未收口 | 仍被 `chat.html:2803`（i18n）与 `:2821`（组件）加载；两个正式模块仍调其失效钩子。删除它的前置正是本文件的「先证明没有数据面调用」，数据面证据已具备，但**删除动作属生产代码改动**，须由 8.6 之前的收口提交完成 |
| 旧开关的独立运行读取 | ❌ 未收口 | §3；前置是阶段 7 运行改造（7.4/7.6 阻塞于真实凭据） |
| 投影里的 `personal.*` 页面 | ❌ 未收口 | `auth/service.py:117-121`、`173-187`、`3451` 起仍逐页签名（其中 `personal.tools` / `personal.skills` 的视图已退役、页面仍被签发）；与组件退役同批处理 |

## 6. 证据命令

```
node --test tests/test_personal_address_forward_frontend.cjs \
  tests/test_account_menu_no_personal_resources.cjs \
  tests/test_personal_console_frontend.cjs
→ tests 70 / pass 70 / fail 0（本周期重跑，非沿用 8.1 的记录）

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_compat_surface_closure.py tests/test_console_migration_drill.py \
  -q -p no:randomly
→ 23 passed
```

## 7. 未覆盖（含原因）

* **生产流量计数**：仓库里没有任何针对旧入口的计数器/日志/审计事件（`rg` 观测点
  只在 `agent/protocol/agent_stream.py:2123` 见一处与工具调用有关的 metric，与旧个人入口
  无关）。所以 8.2 的「调用观测」只能做到**静态 + 断言级**：可以证明「没有代码路径会调用」，
  不能给出「过去一周期内被调用了多少次」。要拿到后者需要先在兼容层埋点，属生产代码改动，
  不在本任务范围。
* **原生 Desktop 的运行时观测**：`desktop/dist/renderer/assets/*.js` 是本地构建产物、
  未纳入版本控制（`git ls-files desktop/dist` 计数 0），无法固定版本，故只记录「本地该产物
  检索不到 `PersonalConsole` / `personal-memory` / `nav:personal` 等令牌」这一事实，
  **不作为验收依据**；受版本控制的 `desktop/`（117 个文件，其中 `desktop/src/**` 96 个）命中 0 已计入 §1.2。
* **真实浏览器复检**：本机无 Playwright（同 `evidence/8-4-frontend-baseline.md`），
  兼容周期的界面观测沿用 3.6 的真实浏览器记录，未在本周期重跑。
* **退役带来的两处前端红（发现即登记，不修）**：本快照重跑 `node --test tests/*.cjs` 得到
  `tests 699 / pass 654 / fail 45`（8.4 基线为 43），多出的 2 条全部落在
  `tests/test_console_i18n_parity.cjs`（`5 项中 2 项失败`）。定位：另一路工作为任务 5.5
  删除了账号命名空间的五个旧个人键（`account_menu_resources`、`_checking`、`_failed`、
  `_retry`、`account_menu_region_personal`），而该用例的**冻结快照**
  （`tests/fixtures/console_i18n_snapshot.json`）仍期望这些键。用同一用例在 HEAD 内容沙箱
  （`/tmp/fe38`，`git archive HEAD channel/web/static/js`）里跑是 `5/5 passed`，说明这 2 条
  红是**本次退役改动引入的**、而非 HEAD 既有失败——它的修法在另一路的范围（更新快照或保持
  键位），本任务不改生产代码与快照，只登记。
