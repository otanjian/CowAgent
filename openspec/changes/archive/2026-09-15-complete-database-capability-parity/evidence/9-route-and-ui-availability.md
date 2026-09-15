# 9. 路由与界面开放一致性（任务 9.1–9.5）

- 记录日期：2026-09-15
- 结论：10 个恢复入口的路由策略、消费者投影与页面投影同源于 `auth/capability_matrix.py`，
  三条投影互相不矛盾；本轮新增两组**控制台侧**验收——载荷层
  `tests/test_recovered_pages_console.py`（任务 9.4/9.5 的"投影与 wire 同一身份"）与前端层
  `tests/test_recovered_pages_frontend.cjs`（任务 9.4 的"控制台怎么用投影"）。9.2/9.3 由既有
  `tests/test_route_registry.py`、`tests/test_recovered_entry_acceptance.py`、
  `tests/test_scoped_project_browse.py` 锁定，本文件只引用不重写。
- 本轮发现 2 处产品缺陷（见第 6 节）。**两处均已修复**（修复在测试之后、由主流程完成，
  未弱化任何用例）：阻塞 1 的 `WeixinQrHandler` 崩溃修好后 `tests/test_weixin_qr_flow.py` 40 例全绿，
  阻塞 2 的记忆页拒绝终态补齐后 `tests/test_recovered_pages_frontend.cjs` 新增两条断言并通过。
  第 6 节保留原始记录（现象、复现、影响），并在每条后追加"修复与复验"小节。
- 说明：本文件写作期间树上有多个代理在并行改动，`channel/web/web_channel.py`、
  `channel/web/static/js/console.js` 的行号发生过漂移；下文引用**符号名 + 当日实测行号**。

## 1. 各子项要求与实现位置

| 子项 | 要求（原文摘要） | 实现位置 | 验收 |
| --- | --- | --- | --- |
| 9.1 | 定时、记忆、项目和扫码的 Web 页面接入独立模块与**权威动作投影**；复用个人菜单迁移结果 | fork 模块 `channel/web/static/js/personal-console.js`；挂载契约 `channel/web/static/js/fragments.js`（`[data-fork-fragment]`）；侧栏与导航门 `console.js::_viewNavDenied` / `_applySidebarPermissions`；定时卡片动作取服务端 `task.capabilities`（`agent/tools/scheduler/authorization.py::TaskAccessService`） | `tests/test_personal_console_frontend.cjs`、`tests/test_scheduler_frontend.cjs`、本轮 `tests/test_recovered_pages_frontend.cjs` |
| 9.2 | 逐方法登记 10 个恢复入口的策略/来源/授权/handler，并更新 `scripts/route-baseline.txt` 的可追溯变化 | `channel/web/route_registry.py::ROUTES`（10 个方法由字面量 `closed` 改为 `S(<slice>, <action>)`）；`scripts/route-baseline.txt` 追加新行、不删历史 | `tests/test_route_registry.py`（**23 passed**，含两张表均由登记派生、重复 pattern 拒绝、`check_route_coverage(vars(web_channel)) == []`） |
| 9.3 | 用真实 `build_web_app()` 验证 10 个方法的合法正向、未登录、非 owner、无资源权与故障路径 | 入口未变（仍是原 handler），策略改为切片派生 | `tests/test_recovered_entry_acceptance.py`（**6 passed**：四个切片全开、逐方法非 `closed`、匿名 401、无租户拒绝、有权限到 handler、无权限被拒）；项目导入的逐请求条件由 `tests/test_scoped_project_browse.py`（**62 passed**）覆盖 |
| 9.4 | 两种导航布局、直链、页内入口、无菜单授权、只读动作与真实 403/503；不预加载拒绝页面、不把无权当版本未开放 | 页内门 `console.js::_viewNavDenied`（读 `console_pages` 的 `menu_denied` / `available` / `read_allowed` / `reason`）；拒绝面 `showUnavailableView`；页内失败面 `loadTasksView` / `loadChannelsView` / `channelsFailureKey` / `_fpBrowse` / `startWeixinQrLogin` | 本轮两个新文件（第 2、3 节） |
| 9.5 | 切片启用/关闭、read/config/execute 与实例连接状态；拒绝"路由已开但投影永久关闭"与"投影已开而接口仍 503" | 单一登记 `auth/capability_matrix.py`（`SLICES` / `route()` / `consumer_availability()` / `page_availability()`）+ `channel/web/route_registry.py::derive_route_policy()` | 本轮两个新文件；登记自身的一致性由 `tests/test_capability_matrix.py`（**19 passed**）锁定 |

## 2. 载荷层验收：`tests/test_recovered_pages_console.py`

做法：真实 `build_web_app()`（`tests/_helpers.py::WebAppHarness`）建四个真实会话
（`operator` = 全量权限 + `menu` 授权；`reader` = 同前但去掉 `agent.use`，即"只读"由**服务端**
决定；`no-menu` = 有功能权限但菜单授权集里没有这两个页面；`bare` = 只有 `chat.use`；
`admin` = 租户管理员），代理读取的载荷一律取 `GET /auth/context` 的真实响应，wire 断言一律
发真实请求。

| 页面 / 切片 | 投影断言 | wire 断言 |
| --- | --- | --- |
| `workbench.schedules` / `scheduler` | `console_pages` 项存在；开的页面 `reason == ""`、`available && read_allowed`；`consumers.scheduler.available` 等于登记的 `enabled` | `GET /api/scheduler`、`POST /api/scheduler/{toggle,update,delete,run}` 的策略逐条等于 `capability_matrix.route()` 派生结果；有权限者非 503，缺 `permission` 者 403，匿名 401 |
| `admin.memory` / `memory_browse` | 同上；`scope == "agent"` | `GET /api/memory`、`GET /api/memory/content` 非 503；无 `memory.read` 时 403；有权限时 `content` 的 400 `invalid_entry` 属 handler 拒绝而非门拒绝 |
| 无页面 / `project_browse` | 登记 `page` 为空，**不得**混进 `console_pages`；`consumers.project_browse` 与 `enabled` 一致 | `GET /api/projects/browse`（read 类）非 503 |
| 无页面 / `weixin_scan` | 同上 | `GET|POST /api/weixin/qrlogin` 非 503、错误码不是门码（见第 6 节阻塞 1） |

关键断言（对应 9.4/9.5 的四个问题）：

1. **一致性**：`test_every_recovered_entry_takes_its_policy_from_the_registry`、
   `test_an_open_entry_is_never_a_gate_refusal_for_a_permitted_caller`、
   `test_a_caller_without_the_declared_permission_is_refused_by_the_gate` ——
   同一身份下"投影说开"与"接口不 503"必须同时成立。
2. **未授权 ≠ 未开放**：`test_a_role_without_the_page_grant_reports_menu_denied`（`reason ==
   "menu_not_granted"`、`menu_denied == true`）、`test_the_menu_reason_is_told_apart_from_a_closed_consumer`
   （部署原因不得是 `menu_not_granted`，`menu_denied` 的页面不得带部署原因）、
   `test_a_menu_denied_page_still_reaches_an_authorized_route`（菜单是导航事实，不是接口授权）、
   `test_an_open_page_reports_no_reason_and_a_closed_one_never_reports_blank`（被拒页面 `reason`
   永不为空、永不为 `deferred`）。
3. **只读只给只读动词**：`test_a_read_only_caller_is_handed_only_the_read_class_verb` ——
   `TaskAccessService` 的 `view/manage/run` 与登记 `open` 类的映射先断言"覆盖的类集合恰好等于
   切片声明的类集合"（登记新增动作时映射过期即失败），再由该映射推出"禁止动词集合"；公共任务对
   `reader` 只给读类动词。`test_the_routes_behind_the_withheld_verbs_refuse_that_task` 用**真实请求**
   证明被扣下的动词确实 403（4 条 config/execute 路由），`test_the_owner_of_a_task_is_handed_the_consequential_verbs`
   做正对照（任务 owner 拿到并调用成功）。
4. **关闭契约**：`test_a_closed_entry_answers_503_with_the_registrys_own_reason`（名字里写明"关闭"）
   与 `test_no_route_declared_closed_in_the_policy_is_ever_served`（对整张策略表派生）。当日实际
   状态是"没有关闭主体"，见第 5 节。

新增用例 20 项（`tests/test_recovered_pages_console.py`）：

- `ConsumerProjectionTests`：`test_every_recovered_slice_reaches_the_console_from_the_registry`、
  `test_a_page_the_registry_owns_is_projected_for_the_console`、
  `test_an_open_page_reports_no_reason_and_a_closed_one_never_reports_blank`
- `WireAgreementTests`：`test_every_recovered_entry_takes_its_policy_from_the_registry`、
  `test_an_open_entry_is_never_a_gate_refusal_for_a_permitted_caller`、
  `test_a_caller_without_the_declared_permission_is_refused_by_the_gate`、
  `test_a_closed_entry_answers_503_with_the_registrys_own_reason`、
  `test_no_route_declared_closed_in_the_policy_is_ever_served`
- `MenuGrantTests`：`test_a_role_without_the_page_grant_reports_menu_denied`、
  `test_the_menu_reason_is_told_apart_from_a_closed_consumer`、
  `test_a_menu_denied_page_still_reaches_an_authorized_route`、
  `test_a_menu_denied_page_clears_its_actions`
- `ReadOnlyActionTests`：`test_the_page_projection_offers_no_consequential_verb`、
  `test_a_read_only_caller_is_handed_only_the_read_class_verb`、
  `test_the_routes_behind_the_withheld_verbs_refuse_that_task`、
  `test_the_owner_of_a_task_is_handed_the_consequential_verbs`
- `PageLessSliceTests`：`test_a_page_less_slice_is_projected_as_a_consumer_not_a_page`、
  `test_the_read_entry_of_a_page_less_slice_is_not_a_gate_refusal`、
  `test_the_project_browse_slice_separates_read_from_execute`、
  `test_the_wechat_qr_entry_answers_from_its_handler_not_from_the_gate`

## 3. 前端层验收：`tests/test_recovered_pages_frontend.cjs`

做法沿用 `tests/test_console_view_registry.cjs`：读源码 + 花括号配对提取函数 + `node:vm` 沙箱真跑
（`VIEW_META` 用**真实字面量**，`_viewNavDenied`/`_consolePageForView` 跑真实函数），断言"源码里
存在的行为"而不是排版。

| 断言 | 用例 |
| --- | --- |
| 四个视图的页键来自服务端投影（`VIEW_META` + `_consolePageForView`），客户端没有自己的页→可用性表，`UNAVAILABLE_VIEWS` 不含这四个视图 | `each recovered view resolves its page key from VIEW_META, not a client list`、`the availability gate reads the projection fields the server signs`、`the recovered views are not in the hardcoded not-shipped set` |
| 无菜单授权 → 由投影判定拒绝（`menu_denied`）；`admin.memory` 关闭 → 投影先拒；未知投影不猜不拦 | `a withheld menu grant denies the recovered workbench page from the projection`、`a closed memory page is refused before the view loads` |
| 拒绝文案来自传入的 `deny.reason`（`nav_denied` 与 `nav_unavailable` 两条），不是写死的"功能未开放" | `a refusal is rendered from the server reason payload, never as a shipped feature` |
| 拒绝页面不预加载：门在派发之前、拒绝分支带 `return`；启动路径不触发这四个页面 | `navigation gates the view before its lazy loader runs`、`the recovered views are not fetched by the startup path` |
| 定时卡片只渲染服务端给该任务的动词（`task.capabilities`），且"关闭"答案不是"成功空列表" | `the scheduler card offers only the verbs the server granted that task`、`a closed scheduler answer is not rendered as a successful empty list`、`a refused scheduler request still resolves to a final state` |
| 无页面的两个入口按服务端答复渲染拒绝：项目目录选择器显示服务端原因而非"空目录"，扫码面板显示服务端原因而非空白/假二维码 | `a refused project browse renders the server reason instead of an empty folder`、`a browsable folder still renders its entries after the fix-for-refusals`、`a refused wechat qr start renders the server reason instead of a fake qr`、`a started wechat qr still renders the qr and starts polling` |
| 恢复页面只有一处可用性来源（fork 模块不复刻），`chat.html` 只有一块"功能未开放"面 | `no fork module carries its own capability list for the recovered pages`、`chat.html keeps one not-available surface instead of per-page "not shipped" copy` |

新增用例 17 项（`node --test` 输出 `tests 17 / pass 17 / fail 0`）。

## 4. 命令与结果（实测输出尾部）

| 命令 | 输出尾部 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_recovered_pages_console.py -q -p no:randomly` | `.................... [100%]` / `20 passed in 1.50s` |
| `node --test tests/test_recovered_pages_frontend.cjs` | `tests 20` / `pass 20` / `fail 0` / `cancelled 0` / `skipped 0` / `todo 0` |
| `.venv/bin/python -m pytest tests/test_route_registry.py tests/test_recovered_entry_acceptance.py tests/test_capability_matrix.py tests/test_scoped_project_browse.py -q -p no:randomly` | `...................................................................  [ 65%]` / `......................................  [100%]` / `110 passed in 78.31s (0:01:18)` |
| `node --test tests/test_console_view_registry.cjs tests/test_scheduler_frontend.cjs tests/test_console_workspace_frontend.cjs tests/test_channel_scope_nav_frontend.cjs tests/test_workbench_menu_grant_frontend.cjs tests/test_recovered_pages_frontend.cjs` | `tests 47` / `pass 47` / `fail 0` / `cancelled 0` |

修复后复跑的汇总（2026-09-15，含第 6 节两处修复）：

```
.venv/bin/python -m pytest tests/test_recovered_pages_console.py tests/test_recovered_entry_acceptance.py \
    tests/test_capability_matrix.py tests/test_route_registry.py tests/test_http_policy.py \
    tests/test_personal_console_pages.py tests/test_plan_3_1_joint_acceptance.py \
    tests/test_upstream_drift_guards.py -q -p no:randomly
→ 139 passed in 85.19s (0:01:25)

node --test tests/test_recovered_pages_frontend.cjs tests/test_fork_fragments.cjs
→ tests 26 / pass 26 / fail 0

node --test tests/test_console_i18n_parity.cjs tests/test_console_view_registry.cjs \
    tests/test_personal_console_frontend.cjs tests/test_personal_console_browser.cjs \
    tests/test_knowledge_console_frontend.cjs
→ tests 78 / pass 78 / fail 0
```

分项计数（同一次运行内单独执行）：`test_route_registry.py` 23 passed、`test_recovered_entry_acceptance.py`
6 passed、`test_capability_matrix.py` 19 passed、`test_scoped_project_browse.py` 62 passed。

## 5. 未能证明的项（明确列出，不臆测）

1. **关闭分支当日无现实主体**：政策表内 `closed` 路由数为 0，`weixin_scan` 的
   `qr`/`poll` 已按 `config` 类开放（实测：`policy=tenant`，投影 `reason=""`）。实测命令与输出：

```
$ .venv/bin/python - <<'PY'
from auth import capability_matrix as cm
from channel.web.route_registry import derive_route_policy
pol = derive_route_policy()
print("closed routes:", len([1 for ms in pol.values() for e in ms.values()
                             if e.get("policy") == "closed"]))
for s in cm.SLICES:
    print(s.id, s.enabled, s.open, s.page, repr(s.reason))
PY
closed routes: 0
scheduler True {'list': 'read', 'toggle': 'config', 'update': 'config', 'delete': 'config', 'run': 'execute'} workbench.schedules ''
memory_browse True {'list': 'read', 'content': 'read'} admin.memory ''
project_browse True {'browse': 'read', 'import': 'execute'} None ''
weixin_scan True {'qr': 'config', 'poll': 'config'} None ''
desktop_tenant_context False {} None 'awaiting_acceptance'
```

   因此 `test_a_closed_entry_answers_503_with_the_registrys_own_reason` 的循环体、
   `test_no_route_declared_closed_in_the_policy_is_ever_served` 的 `checked == 0` 目前**空转**：
   它们断言的是契约，不是当日事实。"关闭消费者 → 503 `database_unavailable`、管理员不可覆写"
   由 `tests/test_http_policy.py::test_closed_consumer_503_in_database`
   （用探针路由主动关闭，理由见该测试注释）证明，本文件不重复。
2. **扫码正向往返未证明**：真实二维码生成与轮询到 `confirmed` 需要一个真实的微信会话，本环境
   没有；阻塞 1 修复后请求可以真正进入状态机（`tests/test_weixin_qr_flow.py` 40 例经真实
   `build_web_app()`），但**真实提供方扫码**仍属任务 7.8，仍不在此证明。
3. **项目导入的正向路径未在载荷层断言**：`POST /api/projects/import/preview` 在非 loopback
   请求上固定 403 `local_path_denied`，`POST /api/projects/import` 需要 preview 签发的一次性
   handle；这两条逐请求条件由 `tests/test_scoped_project_browse.py` 覆盖（引用，未改写）。
4. **两种导航布局的界面级遍历未做（已用源码级断言补上可证部分）**：任务 9.4 提到"两种现有导航布局"。
   本轮补入 `tests/test_recovered_pages_frontend.cjs::both navigation layouts share one gate, and a direct
   link is gated like a click`：布局开关（`web_navigation_mode` 的 `classic`/`split`）**不参与**可用性判定
   （`_viewNavDenied` 不含 `_navigationMode(`），且深链（`#view-*`）、跨区待处理视图与页内点击**都**经
   同一个 `navigateTo` 分发、由同一个门拒绝并渲染服务端原因；两种取值服务同一套 path-based shell 由既有
   `tests/test_web_navigation_mode.py`（7 passed）锁定。仍**未**在真实浏览器里对两种布局各跑一遍点击回归
   （本环境不做浏览器自动化）。
5. **`actions` 词表并非由登记派生**：`console_pages.*.actions` 当日为空对象；"页面级动词为空"
   是断言到的事实，而"若将来非空，其词表必须可追溯到登记"这一点**没有**被测——现有代码没有
   动词名→访问类的映射，无法在不改产品代码的前提下断言。

## 6. 阻塞项 / 产品缺陷（均已修复）

### 阻塞 1（严重，已修复）：`/api/weixin/qrlogin` 的每个已登录请求在进入扫码状态机前就抛异常

- 位置：`channel/web/web_channel.py::WeixinQrHandler._auth_session_id`（今日第 7613 行），
  调用链 `WeixinQrHandler._start` / `._poll` → `._actor`（第 7623 行）。
- 原因：`auth/service.py:2401` 的 `verify_session` 返回 `{"user": user, "session": row}`，而
  `auth/store.py:1337` 设置了 `con.row_factory = sqlite3.Row`，因此 `session["session"]` 是
  `sqlite3.Row`；`((session or {}).get("session") or {}).get("id")` 对 Row 调 `.get()` →
  `AttributeError`。按列名取值（`session["session"]["id"]`）才是可用形式。
- 复现命令（真实 `build_web_app()`，输出为截断）：

```
$ .venv/bin/python - <<'PY' 2>&1 | tail -14
import logging, os, sys, tempfile
sys.path.insert(0, "/Users/jiantan/ai_assistant/cowagent")
logging.basicConfig(level=logging.ERROR)
import config as config_module
tmp = tempfile.mkdtemp(prefix="repro-weixin-")
config_module.conf()["agent_workspace"] = os.path.join(tmp, "cow")
from agent.registry import set_agent_registry
set_agent_registry(None)
from tests._helpers import WebAppHarness
app = WebAppHarness(os.path.join(tmp, "instance"))
app.add_agent("primary")
role = app.role("operator", ["chat.use", "agent.use", "agent.read"],
                grants=[("agent", "agent:primary", "read")])
app.member("operator", [role["code"]])
token = app.login("operator")
r = app.get("/api/weixin/qrlogin", token=token)
print("GET  ->", r.status, r.data.decode())
app.close()
PY
[ERROR][2026-09-15 08:18:15][web_channel.py:7545] - [WebChannel] WeixinQr GET error: 'sqlite3.Row' object has no attribute 'get'
  File ".../channel/web/web_channel.py", line 7723, in _start
    actor = self._actor(ctx)
  File ".../channel/web/web_channel.py", line 7623, in _actor
    auth_session_id=cls._auth_session_id())
  File ".../channel/web/web_channel.py", line 7613, in _auth_session_id
    ident = str(((session or {}).get("session") or {}).get("id") or "")
AttributeError: 'sqlite3.Row' object has no attribute 'get'
GET  -> 400 Bad Request {"status": "error", "message": "the scan request failed", "code": "scan_failed"}
```

- 影响：门（`auth/http_policy.py::enforce_http_policy`）**没有**拒绝这个入口——非 503，但没有
  任何一次扫码请求能走到状态机；异常被 handler 自己的 `except` 吞成 400 `scan_failed`，调用者
  无法区分"版本未开放"、"无权限"和"内部错误"。本轮 `test_the_wechat_qr_entry_answers_from_its_handler_not_from_the_gate`
  只断言门放行（不是门拒绝），因此该缺陷被记录而不是被断言成通过；修好后该用例仍然通过。
- 建议（**已实施**）：`_auth_session_id` 改为按列名取值后，`session["session"]`（`sqlite3.Row`）
  才是可用形式，异常不再被吞成 `scan_failed`；实测请求已能进入状态机并得到真正的业务答复。
- **修复与复验（2026-09-15）**：`tests/test_weixin_qr_flow.py`（40 例，全部经真实 `build_web_app()`
  走完 `GET /api/weixin/qrlogin → POST（轮询/提交）→ 实例落库`）由"32 例失败"变为**全绿**：

```
$ .venv/bin/python -m pytest tests/test_weixin_qr_flow.py -q -p no:randomly
→ 40 passed in 17.60s
```

  该文件即 `auth/capability_matrix.py` 的 `weixin_scan` 切片注释所引用的验收文件，注释与树重新一致；
  R4（扫码一次性与幂等一致）因此由"未通过（部分）"转为**通过**（`evidence/13-review-supplementary-gates.md`）。
  第 5.2 条"真实提供方扫码"仍不在此证明（任务 7.8）。

### 阻塞 2（中，已修复）：记忆页的 403/503 没有页内终态

- 位置：`channel/web/static/js/console.js::loadMemoryView`（今日第 11504 行）与
  `::openMemoryFile`（第 11641 行）：`if (data.status !== 'success') return;`。
- 现象：`/api/memory` 或 `/api/memory/content` 返回 403/503 时，函数直接返回：既没有原因文案，
  也没有空态，页面停在进入该视图时的状态。与另外四个恢复面（`loadTasksView`、
  `loadChannelsView`、`_fpBrowse`、`startWeixinQrLogin` 都会渲染终态原因）不一致。
- 可达性：投影 `admin.memory` 的 `available`/`read_allowed` 通常同向，所以常规导航已被
  `_viewNavDenied` 拦下（本轮已断言该门），但会话中途权限被收回、或按 Agent 作用域被拒时，
  这条静默返回是唯一的"解释"，用户看不到任何拒绝理由。
- 说明：这不是"把无权当成功空态"，而是"没有终态"——同一类问题的另一半。本轮前端文件按现状
  断言了导航门（`a closed memory page is refused before the view loads`），**没有**把缺失的页内
  分支写成通过。
- **修复与复验（2026-09-15）**：`channel/web/static/js/console.js` 新增 `_memoryRefusal(data, opts)`
  统一终态面（清空陈旧行、显示服务端 `message`、区分"这不是『暂无记忆』"、`keepList` 决定是否保留
  目录），`loadMemoryView` / `openMemoryFile` 的非 `success` 分支与 `catch` 分支都改走它，不再静默返回。
  新增两条前端断言后：

```
$ node --test tests/test_recovered_pages_frontend.cjs
→ tests 20 / pass 20 / fail 0
（原 17 例 + 新增 `a refused memory read is a terminal state, never an empty folder`、
  `both memory reads route refusals through that surface and swallow nothing`、
  `both navigation layouts share one gate, and a direct link is gated like a click`）
```

  `tests/test_recovered_pages_frontend.cjs` 与同族前端文件组合运行：
  `node --test tests/test_recovered_pages_frontend.cjs tests/test_fork_fragments.cjs`
  → `tests 25 / pass 25 / fail 0`；Python 侧恢复入口/登记/投影组合
  （`tests/test_recovered_pages_console.py tests/test_recovered_entry_acceptance.py tests/test_capability_matrix.py
  tests/test_route_registry.py tests/test_http_policy.py tests/test_personal_console_pages.py
  tests/test_plan_3_1_joint_acceptance.py tests/test_upstream_drift_guards.py`）→ `139 passed`（见第 4 节更新行）。
