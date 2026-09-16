# 5 回归与兼容

## 5.1 `tests/test_sidebar_account_frontend.cjs`

新增 4 条断言，驱动的是生产 `console.js`：

- `帮助与关于 opens the project site address, not the brand version row`：打开缺省地址 `http://localhost:8080/`；并把 `#sidebar-version` 的 `href` 改成另一个值后再次打开，目标不跟随版本行（证明两者不再共用同一链接），同时断言版本行自身的 `href` 仍是 `https://www.rsm.global/china/zh-hans`。
- `the public brand snapshot supplies the help target when it is usable`：`help_url = https://help.example.com/webhelp` 时打开该地址。
- `an absent or unusable help_url keeps the local development default`：字段缺失、空串、`javascript:alert(1)`、`localhost:8080`（无协议）、`not a url` 五种取值都回退缺省。
- `a failed public brand read leaves the help target usable`：品牌读取抛错后入口仍可用。

```bash
$ node --test tests/test_sidebar_account_frontend.cjs
ℹ tests 59
ℹ pass 54
ℹ fail 5
```

### 这 5 条失败与本 change 无关（改动前即失败）

失败项全部指向已退役的 legacy 身份面：

- `preferences close cleanly at login and remain available in legacy and public modes`
- `startup check failure stays neutral; a visible retry enters database mode and initializes only once`
- `legacy responses distinguish password protection and explicit anonymous access`
- `HTTP, business and incomplete check responses cannot masquerade as anonymous access`
- `legacy login normalizes the status-only success contract and serializes repeated submissions`

它们断言 `_accountState.mode` 为 `'legacy'` 或 `'unknown'`，而生产源码只可能是 `'database'`：

```startLine:58:58:channel/web/static/js/console.js
let _accountState = { phase: 'loading', mode: 'database', authRequired: null,
```

`grep -n "mode: 'legacy'\|mode: 'unknown'" channel/web/static/js/console.js` 无匹配，因此这些断言无法被满足，与本 change 触及的代码（about 目标、`fetchPublicBrand` 追加一行）无关。

反向核对：把本 change 的三处改动（模块级缺省变量、`fetchPublicBrand` 的取用行、`openAccountAbout` 的目标）在 `console.js` 副本里逐一还原后重跑同一份测试，这 5 条**仍然失败**，而本 change 新增的 4 条失败（因为改动已被还原）：

```bash
$ node --test /tmp/precheck/tests/test_sidebar_account_frontend.cjs   # 还原后的副本
ℹ tests 59
ℹ pass 47
ℹ fail 12
# 5 条 legacy 失败原样复现 + 本 change 的 4 条
```

## 5.2 其它前端与 fixture

- `node --test tests/branding_frontend.test.cjs` → 7/7 通过。
- `node --test tests/test_account_menu_no_personal_resources.cjs` → 7/7 通过（账号面板区域顺序 `identity → settings → about → logout → sidebar-version`、`#sidebar-version` 仍在原位）。
- `tests/test_personal_console_browser.cjs:133` 与 `tests/test_appearance_browser.cjs:35` 的 `/api/branding/public` stub 不含 `help_url`：按 4.2 的规则这等价于「未配置」，控制台保留缺省地址；两个文件都不断言帮助入口的目标，也不断言公开投影的精确键集合，因此无需补齐字段。二者只使用 `#sidebar-version` 做布局与对比度检查，该元素未被本 change 改动。
- 本机未安装 Playwright，两个浏览器文件在此环境无法执行（前者自报 `SKIP`，后者在 `require('playwright')` 处即不可用），故只做上述静态核对。

## 5.3 路由与策略

```bash
$ .venv/bin/python -m unittest tests.test_route_registry
Ran 24 tests in 0.008s

OK
$ .venv/bin/python scripts/check-route-coverage.py
route-coverage: 128 routes (68 upstream, 60 fork), 166 method entries
OK
```

本 change 没有新增路由，也没有改变既有路由策略：`/api/branding/public` 的登记行 `RouteEntry("/api/branding/public", "BrandingPublicHandler", "fork:branding", {"GET": P("public", ...)})` 与 `scripts/route-baseline.txt` 中该行都未改动（基线文件的工作区差异来自在途 change `unify-console-by-data-scope`）。

## 5.4 追加字段对既有消费者的影响

`help_url` 是纯追加字段。仓库内 `/api/branding/public` 的消费点只有 `console.js` 的 `fetchPublicBrand()`（浏览器 fixture 除外），桌面端在本仓库没有该端点的调用点或类型定义；不读该字段的消费者行为不变。
