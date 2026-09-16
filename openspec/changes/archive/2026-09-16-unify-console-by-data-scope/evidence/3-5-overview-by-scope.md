# 3.5 概览数据源按范围下发

对应 `tasks.md` 3.5：「改造概览数据源和卡片动作，普通用户只取得本人可见统计，成员数及
公共运行详情不下发，失败不伪装零值」。规范依据
`specs/unified-console-access/spec.md` 的「控制台概览按同一数据范围提供统计」及其两个
场景。

## 1. 前提：成员现在也能进概览

概览是控制台首页（`VIEW_META['admin-home']`）。任务 3.1 把控制台入口改为「有可读的
`admin.*` 页面即可进入」，成员因此也会落在这一页上；旧门禁
`_require_admin_console_access`（平台管理员或租户管理员）会让成员停在一个他能打开、
却答 403 的首页上。

改为 `_require_overview_access`（`channel/web/admin_overview.py:139`）：

* 平台管理员 → 放行（可不带租户，答 `scope: "none"`）；
* 本租户租户管理员 → 放行；
* 其他 → **必须**是本租户的有效成员（`IdentityService.is_member`）。

成员不是被拒，而是被**降级为本人范围**。合法性来自成员资格本身，不来自
`X-Tenant-ID` 头——头是「选择」，不是「许可」，只凭它放行就等于允许任意租户串门。

## 2. 服务端扣留，而不是界面隐藏

`_overview_scope(ctx)` 有两种非空取值：`full`（本租户管理者）与 `self`（其他有效成员）。
它在**聚合之前**判定：

| KPI | `full` | `self` |
| --- | --- | --- |
| `agent_count` | 本租户可管理智能体 | 本人可管理（私有）智能体 |
| `messages_today` | 本租户绑定智能体 | 本人管理范围内的智能体 |
| `member_count` | 本租户有效成员数 | **不聚合**，`null` |
| `system_status` | 部署健康 | 同左（见 §3） |

规范说「不能先向成员返回全租户统计再在界面隐藏」——所以 `member_count` 对 `self` 是
**根本不去查**（`_overview_payload` 里 `if scope == "full"` 才调用
`_gather_member_count`），而不是查出来再置空。少一次查询和少一次下发是同一件事的两面，
前者让后者不可能失误。

`meta.member_count_scope` 因此有三个值，且互相可区分：

* `"tenant"` —— 是你的，这是本租户的数；
* `"not_permitted"` —— 不是给你读的（成员）；
* `"unavailable"` —— 该给你读，但读失败。

把后两者合并会让界面要么谎称读取失败、要么暗示成员本该有权限。

## 3. `agent_count` / `messages_today` 为什么必须换数据源

改前的 `_gather_agent_count` 用 `_tenant_agents_projection(ctx)`（**使用**范围）：对成员
会把共享智能体算进去，即报出不属于他的对象。

改前的 `_gather_messages_today` 遍历 `_tenant_ids_for_context(ctx)`（**租户全部**绑定
智能体）。对成员这会累计**共享智能体**的会话——而共享智能体会话里就是其他成员的消息，
直接违反场景「不收到其他用户消息量」。

两者现在都取**管理范围**（`_iter_tenant_agents(ctx, action=SCOPE_MANAGE)`）：成员得到本人
私有智能体，管理者得到本租户共享加本人。计数本身也是披露——「不能通过统计推断他人私有
记录」（同一规范 §Scenario 搜索及分页）——所以这里不是取「看得见的」而是取「归得着他的」。

会话库当前没有按用户维度（`runs.user_id` 是预留列，仍为空），所以「我能计的消息」暂时
只能表达为「归我的智能体」。等每用户隔离落地后，这里可以收得更细；在更细的事实存在之前
给出更窄的答案，是诚实的那一侧。

### `messages_today` 的取舍：宁可不可用，不给缩水的数

逐个工作区 `continue` 跳过读不到的那个，会得到一个**偏小**的总数并当成正常值返回——这和
返回零是同一种谎，只是数字不同。现在任何一个工作区读失败就让该 KPI 进入 `unavailable`。

### `system_status` 仍对成员下发

判断依据：它是**部署级**的单个枚举值（`ok`/`degraded`），不含租户、成员、智能体或渠道
的任何身份，卡片表达的是「我眼下看到的数字可不可信」。规范所扣留的「公共运行详情」指的
是逐渠道的运行状态，本载荷从未携带。代码注释里写明了这个判断，以便复核。

## 4. 失败不伪装零值

`_overview_payload` 里每个 KPI 各自 `_read(name, gather)`：

* 读成功 → 值（**包括真实的 0**）；
* 读失败 → `None` + 记入 `meta.unavailable`，并 `logger.warning` 出区域名与异常类型
  （报告而非吞掉）。

`kpis.agent_count` / `kpis.messages_today` 因此可能是 `null`，而
`meta.unavailable` 是**机器可读的名字列表**，界面据此本地化区域提示，不必解析人话。

前端 `_adminHomeFormatInt`（`console.js:1599`）修掉一个真实陷阱：
`Number(null) === 0`。旧实现

```js
const x = Number(n);
if (!Number.isFinite(x)) return t('admin_home_kpi_dash');
```

会让服务端新下发的 `null` 渲染成 **"0"**——正是本任务要消除的形状。现在先判 `null`/
`undefined` 再转型，真实 0 仍打印 0，两者保持可区分。

`_renderAdminHomeKpis` 另加区域级提示：`meta.unavailable` 非空时在 KPI 条下方插入一条
「部分数据暂时无法读取 + 重试」，并把失败区域名写进 `data-unavailable`；读取成功的区域
照常可用。`member_count_scope === 'not_permitted'` **不**触发该提示——那不是失败，重试
永远不会变出一个成员数。

## 5. 证据

```
.venv/bin/python -m pytest tests/test_admin_overview.py -q -p no:randomly
→ 12 passed
```

`tests/test_admin_overview.py`（重写）：

* `test_build_overview_tenant_member_scope` —— `full` 才带本租户成员数；
* `test_a_member_is_never_handed_the_tenant_member_count` —— 成员得到 `null` +
  `not_permitted`，且与 `unavailable` 不同码；
* `test_an_unreadable_kpi_is_reported_as_missing_not_as_zero` —— `null` +
  `meta.unavailable`，并显式断言 `!= 0`；
* `test_a_readable_kpi_says_nothing_is_unavailable` —— 真实 0 仍是 0、`unavailable`
  为空（两者必须可区分，否则前一条就成了「把 0 也报成失败」）；
* `test_a_platform_administrator_passes` / `test_a_tenant_administrator_passes` /
  `test_an_active_member_passes` / `test_a_non_member_is_refused` /
  `test_a_caller_with_no_tenant_is_refused` —— 门禁四态。

```
node --test tests/test_admin_home_frontend.cjs
→ 5 passed
```

`tests/test_admin_home_frontend.cjs`（新建）：

* 「不可读的画成 —，绝不是 0」——直接喂 `null`，即旧代码出错的输入；
* 「真实 0 仍打印 0」；
* 「失败区域说明并给重试，成功区域不受影响」；
* 「成员没有成员数时不报读取失败」；
* `unavailable` 是数据不是文案。

```
.venv/bin/python -m pytest tests/test_admin_overview.py tests/test_capability_matrix.py \
  tests/test_console_menu_mapping.py -q -p no:randomly
→ 61 passed
```

三语新增 `admin_home_kpi_unavailable` / `admin_home_kpi_retry`，i18n 快照同步，
`test_console_i18n_parity.cjs` 5 passed。样式 `.admin-home-kpi-note` 落在
`channel/web/static/css/console.css` 概览段落内（含深色）。

## 6. 未纳入本任务

* 「本人范围」的粒度受会话库现状限制（无按用户维度），见 §3 末；
* 卡片动作（快捷入口）本身未改：它们已由任务 3.1 的逐项可用性门禁控制；
* 浏览器内双角色实操属任务 3.6（需 Playwright，本机不可运行）。
