# 8.4b 浏览器用例的口径（runner）与 `test_appearance_browser.cjs` 的重新定性

本文件是 `tasks.md` 8.7 的证据：本机装上 Playwright 之后，(a) 固定浏览器用例的可复制运行
方式；(b) 把 8.4 基线里「`test_appearance_browser.cjs` —— 本机无 Playwright」这条**未经检验的
环境借口**证伪并给出真实原因；(c) 把浏览器用例的实际结果列成清单。

本文件**不改产品代码**，只诊断与记录。

---

## 1. 结论摘要

1. 浏览器用例原本**不是不能跑，是缺驱动包**。完整运行命令只需多一个 `NODE_PATH`
   （见 §2）；`PLAYWRIGHT_BROWSERS_PATH` **不需要**设置（见 §2.3）。
2. 8.4 表里那条「`test_appearance_browser.cjs`：本机无 Playwright」**作废**。装好之后该文件
   确实仍然是失败，但**失败原因完全不同**：`#app` 一直 `hidden`。
3. 真实原因是**整文件陈旧于产品现状**（登录流程前提过期 → 测试从未提交凭据 → 产品当然
   不解除 `#app` 的 `hidden`），**不是产品缺陷，也不是环境**。四项裁定见 §3.4–§3.7。
4. 装上 Playwright 后全量 cjs 失败数 **43 → 44**，**唯一的 +1 就是「用例真的开始执行」**
   （`test_personal_console_browser.cjs` 由 skip 转 fail，原因是它驱动已退役的 DOM）。
   `test_appearance_browser.cjs` 的**条数不变**（1），只有原因变了。（测量途中并行工作区
   曾短暂多出 2 条 i18n 快照漂移，已由另一路修复；见 §5。）
5. 真实缺陷：**本文件范围内没有发现**。发现的是测试与产品口径的陈旧分歧（3 处），以及
   另一名 worker 正在重写的 `test_personal_console_browser.cjs` 的退役 DOM（不属于本文件）。

---

## 2. 可复制的运行方式

### 2.1 命令

```bash
# cjs 全量（8.4 基线用的就是这条）
NODE_PATH="$(npm root -g)" node --test tests/*.cjs

# 单文件
NODE_PATH="$(npm root -g)" node tests/test_appearance_browser.cjs
NODE_PATH="$(npm root -g)" node tests/test_personal_console_browser.cjs

# Python 包装器（它 shell out 到上面的 .cjs）
NODE_PATH="$(npm root -g)" .venv/bin/python -m unittest tests.test_personal_console_browser -v
```

### 2.2 `NODE_PATH` 是**必需**的，不是可选优化

`tests/test_personal_console_browser.py` 的 docstring 说「把 `NODE_PATH` 指向一个 Playwright
安装」——这点是对的，而且是**硬条件**：本仓库**没有 `package.json`、没有 `node_modules`**
（`ls package.json node_modules` → 两者都 `No such file or directory`），Node 的默认解析路径
不会去找全局目录。实测：

```
$ node -e "try{console.log(require.resolve('playwright'))}catch(e){console.log('FAIL: '+e.code)}"
FAIL: MODULE_NOT_FOUND
$ NODE_PATH="$(npm root -g)" node -e "console.log(require.resolve('playwright'))"
/Users/jiantan/.nvm/versions/node/v24.14.1/lib/node_modules/playwright/index.js
```

后果是**静默降级**：包装器 `_playwright_available()` 用 `os.environ.copy()` 把当前环境传给
node，所以只要 `NODE_PATH` 不在环境里，它就走 skip 分支并报 `OK (skipped=1)`——**看上去像
「通过」**：

```
$ .venv/bin/python -m unittest tests.test_personal_console_browser
Ran 1 test in 0.047s
OK (skipped=1)
$ NODE_PATH="$(npm root -g)" .venv/bin/python -m unittest tests.test_personal_console_browser
FAILED (failures=1)          # 不再是 skip，见 §4
```

**结论：`NODE_PATH` 必须出现在环境里**（前缀一次或 `export` 都行；仅在本仓库内没有其它
注入点）。样本命令见 §2.1。

### 2.3 `PLAYWRIGHT_BROWSERS_PATH` **不需要**设置

包装器 docstring 建议「把 `PLAYWRIGHT_BROWSERS_PATH` 指向浏览器缓存」——**实测不需要**，
而且照仓库自己的 `browsers_download_dir()` 去设反而会指到一个不存在的目录：

```
$ ls -d ms-playwright                 # 仓库内 pinned 目录（browser_env.browsers_download_dir()）
ls: ms-playwright: No such file or directory
$ ls -1 /Users/jiantan/Library/Caches/ms-playwright/     # Playwright 默认缓存
b
chromium_headless_shell-1237
chromium_headless_shell-1243
ffmpeg-1011
```

两个用例的浏览器需求不同，但**都不依赖这个环境变量**：

| 用例 | 启动方式 | 解析结果 |
| --- | --- | --- |
| `tests/test_appearance_browser.cjs` | `chromium.launch({ channel: 'chrome' })` | 驱动**系统 Chrome**（`/Applications/Google Chrome.app`），与 Playwright 的下载物无关 |
| `tests/test_personal_console_browser.cjs` | `chromium.launch()` | 默认缓存里的 `chromium_headless_shell-1243`（正是 1.63 需要的构建） |

注意 `chromium.executablePath()` 会报 `/Users/jiantan/Library/Caches/ms-playwright/chromium-1243/...`
（该目录**不在**缓存里），但 headless 启动走的是 `chromium_headless_shell-1243`，所以启动
正常——不要用 `executablePath()` 去判断「浏览器在不在」。

`channel: 'chrome'` 与仓库自身的引擎策略一致（`agent/tools/browser/browser_env.py` 的
`resolve_engine()`：auto 时优先 system-chrome，其次下载的 Chromium）。本机 Chrome 存在：

```
$ ls -d "/Applications/Google Chrome.app"
/Applications/Google Chrome.app
```

### 2.4 可用性证据（版本与启动）

```
$ node --version                         → v24.14.1
$ npm root -g                            → /Users/jiantan/.nvm/versions/node/v24.14.1/lib/node_modules
$ NODE_PATH="$(npm root -g)" node -e "console.log(require('playwright/package.json').version)"
1.63.0
$ .venv/bin/python -m pip show playwright | rg '^(Name|Version)'
Name: playwright
Version: 1.63.0
$ .venv/bin/python -c "import playwright; print(playwright.__file__)"
/Users/jiantan/ai_assistant/cowagent/.venv/lib/python3.14/site-packages/playwright/__init__.py
```

启动实测（两条驱动都自己渲染了内容）：

```
$ NODE_PATH="$(npm root -g)" node -e "const {chromium}=require('playwright');console.log(chromium.executablePath());(async()=>{const b=await chromium.launch();const p=await b.newPage();await p.setContent('<h1 id=t>ok</h1>');console.log('rendered:',await p.innerText('#t'),'| version:',b.version());await b.close();})()"
/Users/jiantan/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing
rendered: ok | version: 153.0.8010.12

$ NODE_PATH="$(npm root -g)" node -e "(async()=>{const {chromium}=require('playwright');const b=await chromium.launch({channel:'chrome'});console.log('chrome channel version:',b.version());await b.close();})()"
chrome channel version: 153.0.8010.36

$ .venv/bin/python -c "from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    b = pw.chromium.launch(); p = b.new_page(); p.set_content('<h1 id=t>ok</h1>')
    print('py rendered:', p.inner_text('#t'), '| version:', b.version); b.close()"
py rendered: ok | version: 153.0.8010.12
```

---

## 3. `test_appearance_browser.cjs` 的重新定性

### 3.1 装上 Playwright 之后的实际输出

```
$ NODE_PATH="$(npm root -g)" node tests/test_appearance_browser.cjs
locator.waitFor: Timeout 10000ms exceeded.
Call log:
  - waiting for locator('#app') to be visible
    24 × locator resolved to hidden <div id="app" class="flex h-screen hidden">…</div>
=== EXIT=1 ===
```

失败点是 `open()`（`tests/test_appearance_browser.cjs:185`）里等待 `#app`。文件在**第 1 个
scenario 就中止**，后面 12 个 scenario（共 13 个）从未被求值。

对照：**没有** `NODE_PATH` 时该文件的失败长这样——

```
Error: Cannot find module 'playwright'
    at Object.<anonymous> (.../tests/test_appearance_browser.cjs:9:22)
  code: 'MODULE_NOT_FOUND'
✖ tests/test_appearance_browser.cjs (48.440625ms)
```

两条错误**不是同一件事**：前者是在真实浏览器里跑到了 DOM 断言，后者根本没进浏览器。8.4 表
记的是后者，用后者去解释前者正是本次要修的错误。

### 3.2 归因链（产品代码读数）

`open()` 的登录块是**条件执行**：

```js
await page.goto(origin + '/chat', { waitUntil: 'networkidle' });
if (await page.locator('#login-tenant-group').isVisible()) {
    await page.locator('#login-tenant-select').selectOption('fixture-tenant');
    await page.locator('#login-btn').click();
}
await page.locator('#app').waitFor({ state: 'visible' });
```

产品侧（`channel/web/static/js/console.js`）：

- 未认证时走 `showLoginScreen()`（`:18513`）→ `_accountHidden('app', true)`，并且
  `_accountHidden('login-username-wrap', false)`、`_accountHidden('login-form', false)`；
  此时 `#login-tenant-group` 是**隐藏**的（HTML 里就带 `class="hidden"`，`:132`）。
- `#login-tenant-group` 只由 `_showTenantPicker()`（`:18608`）解除隐藏，而它的调用点只有两处
  且**都在成功登录之后**：`_submitAccountLogin()` 里 `data.tenants.length > 1`（`:18584`），以及
  `_ensureTenantSelected()` 里 `/auth/me` 的成员数 > 1 且 sessionStorage 无有效租户（`:18484`）。
- `#app` 的解除隐藏只在 `_enterAccountApp()` 的 `initApp()` 之后（`console.js:536`）。
- 登录是**凭据登录**：`_submitAccountLogin()` 在 `if (!pwdInput?.value) return false;`（`:18541`）
  直接返回，不填密码连请求都不会发。

所以：未认证页面上 `#login-tenant-group` **必然隐藏** → 测试的 `if` 为假 → **不填用户名/密码、
不提交** → `#app` 永远 `hidden` → 超时。这就是 3.1 里那 24 次 `resolved to hidden` 的来源。

### 3.3 探针实测（区分「环境 / 产品缺陷 / 陈旧」的判据）

用一个复用同样 fixture 形状的探针（`/tmp/probe-appearance.cjs`，**仓库外**，只读产品代码）
把三种可能分别打出来。默认 identity 是 `public`（未认证）：

| 观测 | 结果 |
| --- | --- |
| 加载后 `#app` / `#login-overlay` / `#login-form` / `#login-tenant-group` | `hidden` / `visible` / `visible` / **`hidden`** |
| `#login-tenant-select` 的 options | `[]`（空） |
| 测试的 if 分支 | 未进入——「test skips the login block entirely -> no credentials are ever submitted」 |
| 走完测试的流程后 `#app` | 仍 `hidden` |
| **填用户名 + 密码再提交后 `#app`** | **`visible`**，`_accountState.phase === 'ready'` |
| `pageerror` | `[]`（整轮 0 条） |
| 登录后 `#login-tenant-group` | 仍 `hidden`——fixture 的 `/auth/login` 只回 **1 个租户**，`length > 1` 不成立 |
| 换 fixture 让 `/auth/me` 回 2 个租户且无已存租户 | `#login-tenant-group` **`visible`**，options `['fixture-tenant','fixture-tenant-b']`；选中并点击后 `#app` `visible` |

最后两行说明：`#login-tenant-group` / `#login-tenant-select` **没有被产品删掉**，仍然可用，只是
**出现在流程的更后面**（凭据之后、且成员数 > 1 且尚未选定租户时）。

### 3.4 裁定一：不是环境

- 环境失败会是启动期错误（`Cannot find module` / 找不到浏览器 / `channel` 不存在）。实测
  已经进入浏览器并跑到 DOM 断言（3.1），且探针里同一条启动路径渲染正常（§2.4）。
- `PLAYWRIGHT_BROWSERS_PATH` 是否设置对结果无影响（§2.3）。
- 结论：**环境已不再是原因**；原判「本机无 Playwright」在这种环境下**不可复现**。

### 3.5 裁定二：不是产品缺陷

- 探针里**凭据登录 → `#app` 解除隐藏 → 成员 1 个时直接进应用 / 成员多个时出现租户选择器 →
  选定后进应用**，全程 `pageerror` 为空（3.3）。
- 也就是说「产品从不解除 `#app` 的 `hidden`」为**假**；只有「测试从不提交凭据」为真。
- 结论：**未发现产品缺陷**。这条很重要：环境借口掩盖的**不是**缺陷，而是**测试与产品的契约
  过期**。

### 3.6 裁定三：既有（pre-existing）——但与「因缺 Playwright」是两件事

用干净 HEAD 内容复核（`git archive HEAD | tar -x -C /tmp/headwt`，不动工作区）：

```
$ cd /tmp/headwt && NODE_PATH="$(npm root -g)" node tests/test_appearance_browser.cjs
locator.waitFor: Timeout 10000ms exceeded.
  - waiting for locator('#app') to be visible
    24 × locator resolved to hidden <div id="app" class="flex h-screen hidden">…</div>
=== HEAD EXIT=1 ===
```

HEAD 与工作区**逐字同一个错误**，且 `git diff HEAD -- tests/test_appearance_browser.cjs` 为空
（本轮没改过它）。所以：**既有** = 真；**原因是「无 Playwright」** = 假。两句话要分开说。

### 3.7 裁定四：陈旧是**多处**的，不是一行过期

为判断「改一行登录前提就能过，还是整文件大面积过期」，在 `/tmp` 里复制该文件并做**定向**替换
（仓库文件**未改动**；`repo` 指回真实仓库以便读到当前产品代码）：

| 变体（/tmp 副本） | 结果 |
| --- | --- |
| A：只把登录块换成「填用户名/密码」 | 前进到 scenario 1 的 `accountPanel()`，在 `#account-menu-prefs` 卡住（`element is not visible`） |
| B：A + 让 fixture 的 `/auth/login` 对任意 identity 都返回 `user` | **scenario 1–3 通过**，在 scenario 4 的第 462 行 `page.locator('#account-menu-tenant').click()` 超时 |

两处补充事实：

- **A 的卡点**源于 fixture 与产品契约不一致：`tests/test_appearance_browser.cjs` 的
  `/auth/login` 只在 `identity === 'database'` 时设置 `login.user`，而默认 identity 是
  `public`；产品侧 `_normalizeAccountCheck()` 在「已认证但无用户名」时给 `phase: 'error'`，
  于是 `_renderSidebarAccount()` 里 `_accountHidden(id, !dbUser)`（`account-menu-prefs` 在 `:239`
  的 id 列表内）把 `个人偏好` 隐藏。补上 `user` 后 scenario 1–3 通过——说明**这个面板本身没坏**。
- **B 的卡点**是**已退役的 DOM**：`rg -o 'id="account-menu-[a-z-]+"' channel/web/chat.html` 的
  结果里**没有** `account-menu-tenant`；产品现在用 `#tenant-menu` + `switch_tenant` 查询参数
  （`console.js:18423`、`:19552`）做租户切换，而测试还在等一个原生 `prompt` 对话框。
  这与 `test_personal_console_browser.cjs` 驱动 `#view-personal-agents` 属于同一类问题。

结论：**整文件陈旧于产品现状**（登录流程前提、fixture 契约、租户切换机制各一处），不是
「一行小改」；`#app` 超时是其中最靠前、最先暴露的一处。

---

## 4. 浏览器用例清单（实际运行结果）

判定「需要真实浏览器」的口径：`rg -ln "chromium\.launch|sync_playwright|async_playwright" tests/`
只命中下面两个 `.cjs`。

| 文件 | 需要真实浏览器 | 现状（设 `NODE_PATH`） | 不设 `NODE_PATH` | 原因分类 |
| --- | --- | --- | --- | --- |
| `tests/test_appearance_browser.cjs` | 是（`channel: 'chrome'`） | **失败**（scenario 1 `open()` 等 `#app` 超时） | 失败（`Cannot find module 'playwright'`） | **陈旧测试 vs 产品契约**（§3）＋**已退役 DOM**（§3.7）；非环境、非缺陷 |
| `tests/test_personal_console_browser.cjs` | 是（`chromium.launch()`） | **失败**：`locator.waitFor: Timeout 15000ms` 等 `#view-personal-agents`（`openPersonalView` `:225`） | **通过**（`SKIP ... playwright is not installed`，exit 0） | **已退役 DOM**（`#view-personal-agents` / `#view-personal-memory` / `#view-personal-channels` 在 `chat.html` 中已不存在，现为 `view-agents` / `view-memory` / `view-channels`）。该文件正被另一名 worker 重写，**本文件只报告、不修改** |
| `tests/test_personal_console_browser.py` | 是（包装上面那个 `.cjs`） | **失败**：`AssertionError: 1 != 0`，转发上面那条超时 | **跳过**：`OK (skipped=1)` | 同上一行；`skip` 是包装器设计（`_playwright_available()`） |
| `tests/test_security_ssrf_browser_navigate.py` | 否（stub 掉 `BrowserService`，文档明说不联网、不用 Playwright） | **通过**（`Ran 12 tests ... OK`） | 通过 | 与本环境无关，装上 Playwright 前后无变化 |
| `tests/test_tool_path_and_eval.py` | 否（只 import `BrowserService`，不启动浏览器；且是 pytest 风格，`unittest` 下 `Ran 0 tests`） | 与本环境无关 | 与本环境无关 | 不在浏览器清单内 |

补充：`.github/workflows/*.yml` 中**没有**任何 `node --test` / Playwright 安装步骤
（只有 release 流程里的 playwright 版本 pin）。也就是说这套 cjs 用例**没有 CI 覆盖**，
`test_appearance_browser.cjs` 的陈旧可以长期不被发现。

---

## 5. 计数变化与归因（8.4 表「43 条」）

同一取样时刻（2026-09-16，工作区含其他 worker 的未提交改动）：

```bash
$ node --test tests/*.cjs                              # 不设 NODE_PATH
→ ℹ tests 699   ℹ pass 656   ℹ fail 43

$ NODE_PATH="$(npm root -g)" node --test tests/*.cjs   # 设 NODE_PATH
→ ℹ tests 699   ℹ pass 655   ℹ fail 44
```

两次运行**只差 `NODE_PATH`**，差量恰好 1 条——这就是这套 harness 的全部影响。43 条的构成：

| 数量 | 文件 | 与本次环境变化的关系 |
| --- | --- | --- |
| 36 | `test_session_history_frontend.cjs`（`queueMicrotask is not defined`） | 不变 |
| 5 | `test_sidebar_account_frontend.cjs`（4 `AssertionError` + 1 `onsubmit`） | 不变（8.4 §更正一已复核） |
| 1 | `_tmp_repro_modeldefaults.cjs`（`document.querySelector is not a function`） | 不变 |
| 1 | `test_appearance_browser.cjs` | **条数不变**（1），但**原因变了**：不设 `NODE_PATH` 时是 `MODULE_NOT_FOUND`（`48.44ms`），设了是 `#app` 超时（`14.27s`） |
| **+1** | `test_personal_console_browser.cjs` | **唯一增量，且与产品/测试内容无关，只与「能否执行」有关**：不设 `NODE_PATH` 时打印 `SKIP ... playwright is not installed` 且 exit 0（计 pass）；设了才真的执行，并在已退役的 `#view-personal-agents` 上超时（`21.51s`） |

即：**43 → 44，`+1` 完全由「浏览器用例真的开始执行」解释**，没有「本轮改动弄坏浏览器用例」
的迹象；同时 `test_appearance_browser.cjs` 的失败**换因不换数**（这正是不该用「1 条」去核对
归因的原因——条数相同，性质完全不同）。

> 测量过程中的插曲（只影响引用方式，不影响结论）：第一次测量时 `test_console_i18n_parity.cjs`
> 另有 2 条失败（`fail 46 / 45`），原因是并行工作区的 i18n 注册表与
> `tests/fixtures/console_i18n_snapshot.json` 漂移；该漂移随后由另一路 worker 修复
> （`node --test tests/test_console_i18n_parity.cjs` → `5/5`），上表为修复后的复测值。
> 与浏览器环境无关，但说明**在并发工作区里引用计数必须带取样条件**。

---

## 6. 未覆盖 / 未验证（含原因）

- **`test_appearance_browser.cjs` 的 scenario 2–13 从未被求值**：原样运行时文件在 scenario 1 的
  `open()` 就抛错（13 个 scenario 里只碰到第 1 个，且没跑完）。§3.7 的 B 变体也只推进到
  scenario 4 就中止，因此 **scenario 5–13**（团队邀请与安全投影、首页布局/上传/发送、多标签
  同步、localStorage 读写失败、本地偏好、会话过期、流式与附件保留、多语言 × 调色板 × 视口
  视觉矩阵与对比度审计）**全部未验证**，不能声称「其余都过」。
- **§3.7 的 B 变体里 scenario 4 的失败未做完整根因**：只确认 `#account-menu-tenant` 不在
  `chat.html` 的 DOM 里、测试仍在等原生 `prompt`。产品当前租户切换的完整交互（`#tenant-menu` +
  `switch_tenant`）未逐步复现。
- **A 变体的卡点归因基于代码读数＋变体 B 的对照**（补齐 `user` 后场景 1–3 通过），没有
  逐帧抓取 `_renderSidebarAccount()` 的调用序列。
- **视觉/对比度产物未评审**：测试会写 `results.json` 与截图到 `COW_APPEARANCE_BROWSER_OUTPUT`，
  本轮只取「是否通过」，没有人工评审截图质量。
- **产品内的浏览器工具（`agent/tools/browser/**`）未做端到端验收**：本轮只验证了
  Playwright 驱动可启动，没有跑真实 Agent 的 `browser.navigate/snapshot` 流程。
- **`test_personal_console_browser.cjs` 只运行、未修改**（另一名 worker 正在重写，02:38 仍在
  活动）；其「退役 DOM」判定来自 `chat.html` 现存 id 与报错定位的一致性，未去核对**新**契约
  应该长什么样。
- **§3.3 的探针与 §3.7 的 A/B 变体都是 `/tmp` 下的一次性脚本，未提交仓库**（本任务的写入范围
  只含本 evidence 文件与 8.4 的表格行）：`/tmp/probe-appearance.cjs`、`/tmp/apertest*/`。结论可
  按 §2 的命令重新依赖，但探针本身需要重写。
- **未在 9899 真实服务上复跑**：3.6 的真实浏览器双角色验收具备可执行条件了，但本文件范围
  只到「本机 fixture 用例」，真实服务复检不在本次交付内。
