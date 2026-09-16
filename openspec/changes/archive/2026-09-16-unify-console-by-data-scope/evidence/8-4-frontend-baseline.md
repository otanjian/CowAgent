# 8.4（基线）前端回归现状：哪些失败是既有的

对应 `tasks.md` 8.4 的前半：「对授权、HTTP、浏览器、i18n、路由基线和真实渠道执行完成有
针对性的回归」。本文件只记录**前端基线**，用于把「本来就坏」与「本轮弄坏」分开；它不是
8.4 的完成证据。

## 命令与结果

```
node --test tests/*.cjs
→ ℹ tests 668   ℹ pass 625   ℹ fail 43
```

（取样时刻。本轮回归清理后再跑一次：`ℹ tests 670  ℹ pass 627  ℹ fail 43`——多出的 2 条是
任务 6.1 空态范围断言，失败数不变，见 `evidence/3-6-browser-dual-role-acceptance.md`。
**2026-09-16 重算（8.9）：上面这些数字的测量条件已不存在，基线作废，见下方「更正三」。**
旧基线是在 **Playwright 驱动缺失、所有浏览器用例静默跳过（跳过计为通过）** 的条件下测得的。
当前条件（`NODE_PATH="$(npm root -g)"`，浏览器用例真的执行）实测：
`ℹ tests 708  ℹ pass 664  ℹ fail 44`；不设 `NODE_PATH` 时为 `ℹ tests 708  ℹ pass 665
ℹ fail 43`（`test_personal_console_browser.cjs` 退回 `SKIP`，计 pass）。总数 708 而非 699，
是因为并行工作区新增了 `tests/test_resource_detail_frontend.cjs`（9 条，本次实测全通过）。）

**新条件下的 44 条**按原因分类（`sed -n '/^✖ failing tests:/,$p'` 后逐条归并）。每行的
「判定」都注明是**本次实测**还是**沿用 8.4b 已有探针**；未验证者明写：

| 数量 | 错误 | 位置 | 判定（新条件下） |
| --- | --- | --- | --- |
| 36 | `ReferenceError: queueMicrotask is not defined`（36/36 同一错误） | `test_session_history_frontend.cjs` 的会话历史用例 | **沙箱缺口（本次实测）**：产品 `console.js` 调用 `queueMicrotask`（1 处），而该用例的 `vm.createContext({...})` 白名单未提供该全局 → 是用例自身沙箱缺东西，与产品行为无关。**非缺陷** |
| 5 | 4 条 `AssertionError`（`:302` / `:358` / `:390` / `:410`）+ 1 条 `TypeError: node(...).onsubmit is not a function` | `test_sidebar_account_frontend.cjs` | **陈旧测试 vs 产品契约（本次实测）**：用例仍断言 `mode === 'legacy'` / `'unknown'` 与**不含 `identity_mode`** 的 "legacy" 响应体；产品 `_normalizeAccountCheck()`（`console.js:465`）只接受 `identity_mode === 'database'` 且恒返回 `mode: 'database'`，后端也只下发该值（`channel/web/auth_handlers.py:383`），`identity_mode=legacy` 开机即拒（`config.py:301`、`common/startup_hooks.py:165`）。原表 §更正一 说这 4 条「在**产品逻辑上**失败」，容易被读成产品问题——**已更正**：产品是 fail-closed 的，`response(database(), 503)` 在 `console.js:669` 就被 `if (!response.ok) throw` 拦住，不会冒充有效会话。第 5 条 `onsubmit` 是**同一原因的下游**：legacy 响应体被判非法 → 进错误门 → 仅有的两处
`login-form.onsubmit` 赋值点（`_clearTenantPicker()` `console.js:438`、`_showTenantPicker()`
`:18640`）都没跑到，于是测试的 `login()` 助手上没有该处理器。**非缺陷** |
| 1 | `TypeError: document.querySelector is not a function` | `identity-admin.js:3527`（`_localizeRoleEditorChrome`），由 `tests/_tmp_repro_modeldefaults.cjs` 触发 | **沙箱缺口（本次实测）**：该用例的 DOM 桩未实现 `document.querySelector`；文件本身是仓库里**已提交**的 TEMP repro（`git ls-files` 确认；提交 `b1b60f0`，2026-09-09「wip checkpoint」）。**非缺陷** |
| 1（整文件） | `locator.waitFor` 超时：`#app` 一直 `hidden`（本次全量取样 `24 ×`；另一次取样 `19 ×`——该次数是 10s 超时内的重试次数，会浮动） | `test_appearance_browser.cjs` | **陈旧测试 vs 产品契约 + 已退役 DOM**：原判「本机无 Playwright」**作废**——装上驱动后该文件仍失败，但错误类型从加载期 `Cannot find module 'playwright'` 变成浏览器内 DOM 超时，所以「条数没变」**不能**推出「归因没变」。根因（`open()` 以 `#login-tenant-group` 可见为登录前提，而该组只在成功登录之后出现 → 测试从未提交凭据）**沿用 `evidence/8-4b-browser-harness-and-reclassification.md` §3 的探针结论**；本次我只复核了该错误在本机可复现，**未**独立重跑 8.4b 的 `/tmp` 探针。**非环境**（已进浏览器）；「非缺陷」由 8.4b 探针验证 |
| 1（整文件） | `locator.waitFor` 超时：`#view-personal-agents` | `test_personal_console_browser.cjs` | **已退役 DOM（本次实测）**：`channel/web/chat.html` 已无 `view-personal-*`（现存统一 id：`view-agents` / `view-memory` / `view-channels` / `view-skills`）。该文件**正被另一名 worker 重写（in-flight，归其所有）**；本表只记录当前状态，不修改、也不裁定其最终契约。**非缺陷（就产品而言）** |

**44 条中没有一条由本轮改动引入，也没有一条被判定为真实产品缺陷**（原表只对 43 条说过这
句话，且用了「全部是环境/沙箱缺口」的笼统口径，现拆分）。但**「既有」不等于「环境缺口」**：

- 42 条（36 `session_history` + 5 `sidebar_account` + 1 `_tmp_repro`）在**干净 HEAD 检出**上
  逐条复现（`git archive HEAD` → `/tmp/84head`，同三个文件 `ℹ tests 101 / pass 59 / fail 42`），
  属既有沙箱缺口 / 陈旧契约。
- 其余 2 条（两个浏览器文件）是**原先被环境掩盖、现在才真正执行**的用例——「既有性」已由
  8.4b §3.6 / §4 单独复核，且原因与原判（「本机无 Playwright」）**完全不同**。如果沿用原表，
  这两条会被错误地当作环境噪音跳过。

### 更正一：`test_sidebar_account_frontend.cjs` 的 5 条不是沙箱缺口

首版把该文件的 5 条一律标成「缺 `onsubmit`」，那是按错误类型粗看的误标。实际是
**4 条 `AssertionError` + 1 条 `onsubmit`**，且失败的 4 条断言（`preferences close cleanly`、
`startup check failure stays neutral`、`legacy responses distinguish password protection`、
`HTTP, business and incomplete check responses cannot masquerade as anonymous access`）
在**产品逻辑上**失败——例如最后一条要求无效/不完整的 `/auth/check` 响应不得被当作有效
数据库会话（期望 `mode === 'unknown'`，实得 `'database'`）。

用 HEAD 干净工作树（`git worktree add --detach /tmp/headwt HEAD`）跑同一文件复核：
**HEAD 同样是这 5 条失败、名字完全一致**。所以它们是**既有**的产品/测试口径分歧，
不是本轮引入；但也**不是**沙箱缺东西——本表首版用「环境缺口」解释它们，会掩盖以后真
的回归，故在此更正。本 change 不处理它们（账户面板语义不属于本 change 的范围）。

### 更正：`test_appearance_browser.cjs` 的失败**不是**因为「本机无 Playwright」

首版把该文件的 1 条失败归因为「本机无 Playwright，与任务 3.6 同一原因」。这条解释**只做到了
自洽，没有检验是否为真**（与 3.6 已更正的同一类错误）。Playwright 装上后该解释即可证伪：

```
$ NODE_PATH="$(npm root -g)" node tests/test_appearance_browser.cjs
locator.waitFor: Timeout 10000ms exceeded.
Call log:
  - waiting for locator('#app') to be visible
    24 × locator resolved to hidden <div id="app" class="flex h-screen hidden">…</div>
=== EXIT=1 ===
```

即「失败」依旧成立，但**错误类型换了**：不再是 `Cannot find module 'playwright'`（加载期），而是
真实浏览器里的 DOM 断言超时。三项裁定：

- **非环境**：已经进入浏览器并跑到 DOM 断言；`PLAYWRIGHT_BROWSERS_PATH` 设与不设无差别
  （该文件用 `chromium.launch({ channel: 'chrome' })`，驱动系统 Chrome）。
- **非产品缺陷**：探针实测「填用户名 + 密码提交 → `#app` 解除隐藏 → 成员多个时出现租户选择器 →
  选定后进应用」全程可用，`pageerror` 为 0。产品不会「永远不解除 `#app` 的 `hidden`」。
- **是测试陈旧**：该文件的 `open()` 只在 `#login-tenant-group` 可见时才登录，而产品把该组
  **放在成功登录之后**（`console.js:18608` 的 `_showTenantPicker()`，只在
  `:18584`/`:18484` 两处、即已认证且成员数 > 1 时解除隐藏；`:18541` 的
  `if (!pwdInput?.value) return false;` 使无密码提交根本不发请求）。于是测试**从未提交凭据**，
  `#app` 超时可预期。把登录前提改对后（`/tmp` 副本，仓库文件未动）该文件能跑过 scenario 1–3，
  之后在 scenario 4 卡在 `#account-menu-tenant`——该 id **已不在 `chat.html`**（产品改用
  `#tenant-menu` + `switch_tenant`）。故陈旧是**多处**的，不是一行小改。

**既有性**：用干净 HEAD 内容复核（`git archive HEAD | tar -x -C /tmp/headwt`）得到逐字相同的
`#app` 超时，且 `git diff HEAD -- tests/test_appearance_browser.cjs` 为空。所以
**「既有」为真、「因缺 Playwright」为假**——两句话必须分开说。

**计数**：同一取样时刻，不设 `NODE_PATH` 为 `ℹ tests 699 / pass 656 / fail 43`（与本节既有
基线逐项一致）；设 `NODE_PATH` 后为 `ℹ pass 655 / fail 44`。（**8.9 复测更新总数**：并行工作区
其后新增 9 条 `tests/test_resource_detail_frontend.cjs`，同一因果下现为
不设 `NODE_PATH` → `708 / 665 / 43`、设 `NODE_PATH` → `708 / 664 / 44`；见「更正三」。）
**两次只差 1 条**，即
`test_personal_console_browser.cjs`：它原先打印 `SKIP ... playwright is not installed` 且 exit 0
（计 pass），现在真的执行，并在已退役的 `#view-personal-agents` 上超时。
`test_appearance_browser.cjs` **换因不换数**——仍是 1 条，但错误由加载期的
`Cannot find module 'playwright'`（`48.44ms`）变成 `#app` 超时（`14.27s`），所以**不能用「条数
没变」来判定归因没变**。

完整的 runner 命令、可用性证据、清单与未覆盖项见
`evidence/8-4b-browser-harness-and-reclassification.md`。

## 本轮新增/受影响的前端用例

```
node --test tests/test_personal_address_forward_frontend.cjs \
  tests/test_memory_target_picker_frontend.cjs tests/test_admin_home_frontend.cjs \
  tests/test_console_i18n_parity.cjs
→ 全通过
node --test tests/test_personal_console_frontend.cjs tests/test_nav_area_frontend.cjs \
  tests/test_channel_scope_nav_frontend.cjs tests/test_admin_area_group_gating.cjs \
  tests/test_account_menu_no_personal_resources.cjs tests/test_recovered_pages_frontend.cjs \
  tests/test_tenant_channel_frontend.cjs tests/test_tenant_channel_card_frontend.cjs \
  tests/test_channels_page_header_frontend.cjs
→ 全通过
```

## 为什么先记下来

「43 条失败」这个数字本身没有意义，**哪 43 条**才有意义。8.4 收口时需要的判断是
「失败集合是否变大」，所以先固定基线。若后续把 `queueMicrotask` 补进沙箱或装上
Playwright，本表要同步更新——那时它们就从「既有」变成「已修」。

## 更正：基线之后出现的 i18n 快照漂移（已修）

上文把 `test_console_i18n_parity.cjs` 列入「全通过」，那只对本基线**取样时刻**成立。
基线之后，4.4/4.6 的 `agents_set_my_default_*`、`agents_anchor_source_*`，5.1 的
`memory_target_personal`，以及 6.1 的 `tenant_channel_self_desc` 等键陆续加入注册表，
而 `tests/fixtures/console_i18n_snapshot.json` 未同步，该用例转为**失败**。

用 HEAD 干净检出（`git archive HEAD` → 临时目录）复核：**HEAD 时该用例通过**，所以这
不是既有环境缺口，而是本工作区未提交改动造成的真实漂移——**上面「没有一条来自本轮改动」
这句话只对那 43 条成立，不适用于此条**。漂移内容与修复见
`evidence/3-6-browser-dual-role-acceptance.md` §4。

修复后 `test_console_i18n_parity.cjs` 5/5 通过。教训：本表把「43 条」当作全量环境缺口，
但前端回归里既有**环境缺口**也有**未同步的快照/声明**，两者必须分开判定，不能因为
「沙箱缺东西」的印象就跳过整批用例。

## 更正二：上述「已修 / 5/5 通过」在核对时不成立，漂移被低估（2026-09-16）

上一节的结论有两处错：

1. **「修复后 5/5 通过」不可复现**。本次核对时 `tests/fixtures/console_i18n_snapshot.json`
   并不含 3.3 证据、`3-6` §4 与归档 `upgrade-personal-channel-workbench` §3.6 三处声称已同步
   的键；该文件当时只带另一路在途改动新增的 18 个 `resource_detail_*` / `models_catalog_*`
   键（`git diff --numstat` = `54 / 3`）。
2. **规模被低估**。实测漂移是 **74 个键级 delta × 3 语**（新增 68 / 删除 5 / 改值 1），
   不是 `evidence/8-2-compat-cycle.md` §7 所判的「多出的 2 条红由 5 个 `account_menu_*`
   键造成」；它跨 3.2 / 3.3 / 3.4 / 3.5 / 4.4 / 4.5 / 4.6 / 5.1 / 5.1b / 5.4 / 6.1 与一个已归档
   change。逐键归属表、三语齐备性检查（新增键无缺口；zh-Hant 7 个既有缺口如实登记）
   与「用例仍有牙齿」的变异验证见 `evidence/8-4c-i18n-snapshot-reconciliation.md`。

修复后：`node --test tests/test_console_i18n_parity.cjs` → 5/5；
`node --test tests/*.cjs`（**不设** `NODE_PATH`）→ `tests 708 / pass 665 / fail 43`（8.9 复测；
原记 `699 / 656 / 43` 的取样更早，其后并行工作区新增了 9 条 `tests/test_resource_detail_frontend.cjs`，
本次实测全通过），43 条逐项等于本表既有基线（**仅限该条件**：设 `NODE_PATH` 后是 44 条，
多出的正是 `test_personal_console_browser.cjs`，见「更正三」），
`test_console_i18n_parity` 不再出现在失败集（此前取样为 `654 / 45`）。

---

## 更正三（2026-09-16，对应 `tasks.md` 8.9）：43 不再是有效基线，已在新条件下重算

**条件变了，所以必须重算，而不是打补丁。** 旧基线（`668 / 625 / 43`，后记 `670 / 627 / 43`、
`699 / 656 / 43`）是在 **Playwright 驱动缺失、所有浏览器用例静默跳过、跳过计为通过** 的条件下
测得的。该条件**已不存在**：`playwright==1.63.0` 已装（Python 在 `.venv`，Node 为全局包，经
`NODE_PATH="$(npm root -g)"` 解析），驱动可启动 Chromium 153 并渲染；本仓库无 `package.json`
/ `node_modules`，故 `NODE_PATH` 是**硬条件**而非可选优化——不设它，浏览器用例只会静默 `SKIP`
而不会被发现。旧的 43 与它的分类表因此一概作废，**上表已按新条件替换**。

### 重算命令与取样时刻

```
$ NODE_PATH="$(npm root -g)" node --test tests/*.cjs
→ ℹ tests 708   ℹ pass 664   ℹ fail 44   ℹ skipped 0

$ node --test tests/*.cjs           # 不设 NODE_PATH
→ ℹ tests 708   ℹ pass 665   ℹ fail 43   ℹ skipped 0
```

取样时刻 **2026-09-16 03:04:02–03:04:24 +0800**，仓库 `HEAD = cbdb9884`（工作区含其他 worker
未提交改动）。**这是快照**：树在并发移动，重跑前请重新取样。为判定快照是否稳定，本次在运行
前后各取一次哈希，两者逐项相同（`tests/test_appearance_browser.cjs` `f88974f4…`、
`tests/test_personal_console_browser.cjs` `2085601d…`、`tests/test_console_i18n_parity.cjs`
`dc0e38af…`、`tests/fixtures/console_i18n_snapshot.json` `b26fbb2e…`、
`channel/web/static/js/console.js` `39884b25…`，均为 16 位前缀）。

与旧记 `699 / 655 / 44` 的差异只有总数与 pass：**+9 条全部来自并行工作区新增的
`tests/test_resource_detail_frontend.cjs`**（实测 9 条全通过），**失败数同为 44**。所以「44」
这个失败集合与 8.4b §5 的复核一致。

### 本次实测 vs 仅属假设

| 结论 | 本次是否实测 | 依据 |
| --- | --- | --- |
| 两个计数值、以及 `NODE_PATH` 与 +1 的因果 | **实测** | 两种条件各跑一次全量，差正好 1 条 |
| 42 条非浏览器失败是**既有** | **实测** | 干净 HEAD 检出（`/tmp/84head`）同三文件 `101 / 59 / 42` |
| 36 条 `queueMicrotask` 是**用例沙箱缺口** | **实测** | 产品有该调用，用例 `vm.createContext` 白名单里没有；36/36 同一错误 |
| 5 条 `sidebar_account` 是**陈旧测试 vs 产品契约** | **实测（代码路径读数）** | 见上表该行；产品 fail-closed，`legacy` 模式已退役且开机即拒 |
| `_tmp_repro_modeldefaults` = **已提交 TEMP repro + DOM 桩缺 `querySelector`** | **实测** | `git ls-files` + 提交 `b1b60f0`（2026-09-09）+ 错误栈 |
| `test_personal_console_browser.cjs` 驱动**已退役 DOM** | **实测** | 超时在 `#view-personal-agents`；`chat.html` 已无 `view-personal-*`。但该文件归并发 worker，**未**核对其目标新契约 |
| `test_appearance_browser.cjs` 的 `#app` 超时**可复现** | **实测** | 本机运行得到同一超时 |
| `test_appearance_browser.cjs` 的**根因**（登录前提过期）与**非缺陷** | **未独立验证，沿用 8.4b 探针，hypothesis-only for this table** | `evidence/8-4b-browser-harness-and-reclassification.md` §3.3–§3.7；本次未重跑其 `/tmp` 探针 |

### 过程中的两个并发发现（只登记，非本表结论）

1. **移动靶**：03:01:19 的首次全量取样为 `708 / 661 / 47`，多出的 3 条来自**未跟踪且正在被
   写入**的 `tests/test_resource_detail_frontend.cjs`（其 mtime 在取样期间从 `03:01:50` 走到
   `03:02:05`）；到 03:04 稳定取样时它已被其作者修好，失败集回到 44。即**任何数字都必须连
   条件与并发状态一起读**。
2. **i18n 快照在本次运行期间未再漂移**：`test_console_i18n_parity.cjs` 稳定 5/5，快照哈希
   `b26fbb2e…` 运行前后不变。按 8.9 的 recovery guard，**没有**出现「并行整文件写入覆盖掉
   8-4c 刚完成的对账」的情况。

### 仍未验证 / 未覆盖

- `test_appearance_browser.cjs` 的 scenario 2–9 从未被求值（文件在第 1 个 scenario 就抛错），
  **不能**声称「其余都过」；其变体推进上限见 8.4b §6。
- `test_personal_console_browser.cjs` 只运行、未修改、未核对新契约（归并发 worker）。
- 「非缺陷」的判定依赖代码路径读数与 8.4b 探针，**不是**端到端真实服务复检；真实服务上的
  浏览器双角色验收（3.6 未做的那一半）仍待补。
