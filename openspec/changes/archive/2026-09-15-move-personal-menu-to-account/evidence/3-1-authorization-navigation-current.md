# 任务 3.1–3.5 — 授权、受保护导航与当前项同步

任务要求：个人入口过滤与旧主导航选择器解耦、复用既有能力投影并支持完整重算/空组移除/撤权后
恢复；接入检查中/失败/重试与强制改密、无租户、未登录限制且开面板不预加载个人页面；账号个人
入口接入既有受保护导航且取消/拒绝/过期目标不先切区域、不启动消费者、离页只执行一次；提交后
唯一当前项标记、区域提示、标题/面包屑一致且重开面板可见；账号/租户变化、退出、资格失效时清除
过期入口与标记并防晚到结果。

## 1. 过滤解耦与完整重算（3.1）

`console.js`：

- `_consolePageEntryState(viewId)`（314–333）：唯一的授权判定入口，读
  `_baseAuthContext().console_pages[_consolePageForView(viewId)]`；`menu_denied === true` 或
  `reason === 'capability_disabled'` 直接隐藏，`admin.*` 页再按 `available`/`read_allowed`
  与 `authorization_mode === 'all'` 判定，其余页（workbench/个人页）默认可见。`known: false`
  表示后端未签名该页，**不猜测隐藏**。
- `_renderAccountResources()`（338–373）：每次从 `_accountPersonalEntries()` 取实时节点并按
  `_consolePageEntryState` **重算** `hidden`（不累积），因此撤权隐藏、再授权恢复；可见项为 0
  且不在检查中/失败态时，`#account-menu-resources` 整体隐藏（空分组移除）。
- `_accountPersonalEntries()`（289–296）只查 `#account-menu-resources .account-menu-personal`，
  不再依赖 `#sidebar-nav` 选择器；`_applySidebarPermissions`（18180–18285）在既有逐项判定后
  调用 `_renderAccountResources()` + `_syncAccountPersonalCurrent()`（18282–18283），把
  「谁的投影」保持为唯一一份，两个宿主不可能给出不同结论。

未新增角色推断、grant、权限或页面能力副本：个人入口只消费 `/auth/context` 的
`console_pages`（既有 `menu_denied` / `available` / `read_allowed` / `reason` / `scope` 字段）。

## 2. 检查中 / 失败 / 重试，以及不预加载（3.2）

- `_accountProjectionPhase()`（304–312）：`ready`（已有投影）/ `failed` / `checking` /
  `unknown`（尚无权威回答），全部按「未确认」处理，不按 legacy 或空租户猜测开放。
- `_renderAccountResources` 后段（358–374）：投影未确认（`checking`/`unknown`）或**当前没有已选
  租户**时隐藏全部入口并显示 `account_menu_resources_checking`；`failed` 时显示
  `account_menu_resources_failed` + `#account-menu-resources-retry`。未确认的入口永不可激活。
  「无有效租户」因此与「投影未确认」同解：入口/设备上没有可点击的个人项，也不会发出个人数据
  请求；真正受限的租户选择与强制改密流程仍由既有门控制（本 change 未新增旁路）。
- `refreshAccountResources()`（378–388）：先 `_invalidateAuthContext()` 并把
  `_authContextPhase = 'checking'`（`_authContextSeq` 自增使旧回复作废），再
  `_fetchTenantAuthorization()`，成功后 `_applySidebarPermissions`。**只**重读租户能力摘要，
  不触碰身份、不请求任何个人页面数据。
- 强制改密 / 未登录：`applies = isDb && hasUser`（350）为假时整组隐藏，沿用既有强制改密门，
  未新增旁路；数据库模式但**没有已选租户**时按未确认态处理（入口不可激活，见上一条）。
- 只展开面板不启动消费者：`toggleAccountMenu()`（573–592）只做挂载、重绘、限高、滚动与焦点，
  不调用任何 view 加载器；`navigateTo` 才 `_loadRegisteredView(viewId)`。

## 3. 受保护导航（3.3）

`navigateTo(viewId)`（1912–）：顺序为

1. `scenarios` 别名与 `UNAVAILABLE_VIEWS` 占位 → 2. `VIEW_META` 未知直接返回 →
3. 权威可用性 `_viewNavDenied(viewId)`，拒绝则 `showUnavailableView(viewId, reason)` 返回 →
4. **离页检查** `if (!_viewLeaveApproved(viewId) && !_viewLeaveCheck(viewId)) return;`（1937）→
5. 跨区域分支：写 pending、`_navApprovedTarget = viewId`、`_openNavArea(want)`（同窗切换，
   内层递归 `navigateTo` 复用批准，不再问第二次）→ 6. 提交：版本自增、`_activateViewContainer`、
   主导航当前项循环、`currentView = viewId` 后 `_syncAccountPersonalCurrent()`、面包屑、
   `_loadRegisteredView`、消费者加载。

`openPersonalEntry(viewId)`（2093–2098）：校验个人 id 与入口存在且未隐藏 → `closeAccountMenu(true)`
（释放面板与移动焦点陷阱，并把焦点交回触发器）→ `navigateTo(viewId)`。这是账号入口唯一的导航
适配器，因此拒绝门、未保存检查与唯一当前项与主导航完全同源。

与旧实现相比修正的顺序问题：原 `navigateTo` 先跨区域后离页检查，会在用户尚未确认放弃草稿时
就 pushState、重渲染区域壳并启动目标消费者；现在离页决定先于一切提交。

## 4. 当前项标记与重开可见性（3.4）

- `_syncAccountPersonalCurrent()`（394–410）：`currentView` 为个人页时，只有**未隐藏**的对应
  账号入口得到 `active` + `aria-current="page"`，其余清除；`#sidebar-account-footer` 同步
  `is-personal` 类，`#sidebar-account-region`（屏幕阅读器区域状态）仅在有标记时可见；主导航
  侧由 `navigateTo` 的 `.sidebar-item` 循环清除所有选中项（个人页不在主导航中）。
- 调用点：`navigateTo` 提交后（1986–1989）、`showUnavailableView`（1863–1882，拒绝/未开放
  目标不保留旧标记）、`_applySidebarPermissions`（18283）、`_renderSidebarAccount`（265–266，
  语言切换/品牌重绘/身份刷新后不丢标记）、`_clearAccountPersonalState` 的反向清理。
- 重开面板可见：`_syncAccountMenuScroll()`（518–523）在 `toggleAccountMenu` 中把
  `aria-current="page"` 的入口 `scrollIntoView({ block: 'nearest' })`，末位入口也能显示。
- 直接链接、重绘、加载失败、返回非个人页：标记由 `currentView` 驱动，非个人页即全部清除；
  `VIEW_META` 缺失或不可用视图走 `showUnavailableView` 清除。

## 5. 上下文变化与晚到结果（3.5）

`_invalidateAccountIdentity(phase)`（605–625）：自增 `_authEpoch` / `_accountCheckSeq` /
`_authContextSeq`，清空 `_authContext`、`_authContextRequest`、`_authContextPhase='unknown'`，
重置 `_accountState`，关强制改密门与租户选择器，`closeAccountMenu()` 并
`_clearAccountPersonalState()`（416–429：隐藏全部入口、移除 `active`/`aria-current`、隐藏分组、
状态、重试、`is-personal` 与区域状态）。

因此：账号切换、退出、资格失效立刻清空入口与标记；晚到的旧 `/auth/context` 回复因
`_authContextSeq` 不匹配被丢弃，旧导航也无法恢复旧页面或旧入口（请求代际保护沿用既有实现）。

## 6. 验证（命令与结果）

| 用例 | 命令 | 结果 |
| --- | --- | --- |
| 账号/面板前端契约 | `node --test tests/test_sidebar_account_frontend.cjs` | 64 用例，59 通过 / 5 失败（迁移前既有失败集合） |
| 渠道作用域导航（共享 `_applySidebarPermissions`） | `node --test tests/test_channel_scope_nav_frontend.cjs` | 8 用例，8 通过 |
| 工作台菜单授权（共享 `_applySidebarPermissions`） | `node --test tests/test_workbench_menu_grant_frontend.cjs` | 9 用例，9 通过 |
| 真实浏览器契约 | `NODE_PATH=… node tests/test_personal_console_browser.cjs` | 24 场景通过 |

前端断言：`the account 「我的资源」 group is recomputed from the authoritative projection`、
`an unconfirmed projection offers no activatable entry and no personal request`、
`an identity without a confirmed tenant offers no personal entry either`、
`a personal page carries exactly one current marker, on its account entry`、
`an account or tenant change clears the entries, the marker and the cached projection`、
`an account entry navigates through the shared protected path, checking the leave once`、
`a cancelled leave keeps the original area, page, address and current item`、
`a denied target renders the denial without starting its consumer`、
`a hidden account entry never navigates, and click/Enter/Space activate the visible ones`。

浏览器新增场景：`opening the panel preloads no personal page`、
`the panel marks the current personal page exactly once`、
`a failed projection offers a retry, never an unconfirmed entry`、
`with the personal console off the empty group is removed, actions stay`。

`a hidden account entry never navigates…` 同时覆盖「撤权后再授权恢复」的另一半：隐藏入口对
直接调用 `openPersonalEntry` 也是惰性的（`calls.closed === 0`、未提交、未切区域）。

## 7. 已知未完成边界（依赖 change，不在本 change 范围）

`tests/test_personal_console_menu.py::ZeroVisibilityRegressionTests` 两个用例在**本 change 开始前
的树上**即失败（根因：内置 role 的菜单默认值未含 `workbench.schedules`，而既有无菜单 grant 的
兼容规则让成员原本可读该页），涉及 `auth/service.py` / `auth/store.py`，属
`enable-member-personal-console` 切片；本 change 未修改任何 Python 文件，不覆盖其实现，
记录于 6.3 交付证据。
