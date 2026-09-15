# 任务 4.1–4.4 — 响应式、焦点与弹层生命周期

任务要求：桌面上方弹层与图标侧栏右侧弹层的可用空间定位、限高与内部滚动；沿用移动断点做底部
弹出面板并补齐标题、关闭按钮、模态语义、安全区与背景交互限制，宿主不受侧栏 transform/overflow
裁剪；桌面 Tab 离开关闭、移动焦点循环、Escape/外部点击关闭、目标页成功聚焦、取消离页恢复焦点、
顶部菜单互斥、隐藏父分组下项目不可聚焦；关闭侧栏/切换断点/退出/认证失效无残留，并适配浅深
主题、现有配色与减少动态效果。

## 1. 桌面与图标侧栏弹层（4.1）

`console.js` `_applyAccountMenuHeight()`（501–516）：

- 移动形态直接清空内联 `max-height`，交给 CSS 的 `80%` 视觉视口限制；
- 桌面展开侧栏：`available = footer.getBoundingClientRect().top - 12`（账号卡上方的空间）；
- 图标侧栏（`#app.sidebar-collapsed`，即面板从图标侧栏侧向展开）：`max(160, innerHeight - 24)`，
  不再受账号卡位置限制。

CSS `console.css`：`.sidebar-account-menu { overflow-y: auto; overscroll-behavior: contain; }`，
内部滚动而不是撑破窗口；`.account-menu-action` 行高与 `min-height: 36px` 保证触摸尺寸。

浏览器用例：`desktop: a short viewport caps the panel and keeps the last entry reachable`
（1440×420：限高 = 账号卡上方空间 ±1.5px、`scrollHeight > clientHeight`、滚到 `personal-skills`
后仍落在视口内）。

## 2. 移动底部弹出面板（4.2）

- 断点沿用既有 `lg`（1024）：`_accountMenuSheetMode()`（455–458）。
- `_mountAccountMenu(isSheet)`（468–489）：窄屏把**同一个** `#sidebar-account-menu` 节点
  `document.body.appendChild`，加 `.account-menu-sheet`、`role="dialog"`、`aria-modal="true"`；
  桌面再挂回 `#sidebar-account-footer`，改回 `role="group"` 并移除 `aria-modal`。宿主改变的原因写在
  注释里：离屏侧栏带 transform，fixed 定位的底部面板会被裁剪并随抽屉一起被拖动。
- 面板头部 `#account-menu-sheet-head`（标题 `account_menu_title`「账号」+ 关闭按钮
  `#account-menu-sheet-close`，`aria-label` 走 `data-i18n-aria-label="close"`）仅在底部形态显示。
- `_accountMenuSheetChrome()`（491–496）：只在该形态显示 `#account-menu-backdrop` 并给
  `body` 加 `account-menu-sheet-open`（`overflow: hidden` 滚动锁）。
- CSS 底部面板：`max-height: 80%`、圆角、`env(safe-area-inset-bottom)` 安全区内边距、
  `.account-menu-backdrop` 覆盖层；`@keyframes accountMenuSheetIn` 进入动画。

浏览器用例：`narrow: the account panel is a modal sheet outside the drawer`（宿主在
`body > #sidebar-account-menu`、`role="dialog"`、`aria-modal="true"`、关闭按钮可见、遮罩可见、
`body` 有滚动锁、五个入口可达）。

## 3. 焦点契约（4.3）

| 行为 | 实现 | 证据 |
| --- | --- | --- |
| Escape 关闭 | `_accountMenuKey`（526–556）：`Escape` 阻止冒泡并 `closeAccountMenu(true)` | `desktop: Escape and an outside click close the panel and return focus` |
| 外部点击/焦点离开关闭 | `_accountMenuOutside`（431–438）以 `pointerdown`(capture) + `focusin` 注册；「内部」同时覆盖面板与 footer，因窄屏面板挂在 body | 同上（点击页面空白关闭）与 `Tab` 越过末项后关闭 |
| 桌面 Tab 离开关闭 | 非模态弹层，`focusin` 到达面板之外即关闭 | `desktop: Escape and an outside click…` 中 `#sidebar-version` → `Tab` |
| 移动焦点循环 | `_accountMenuKey` 在 `_accountMenuSheetActive()` 时对首/末项做 `Tab`/`Shift+Tab` 环绕，并把面板外焦点拉回首项 | `the mobile account panel is a modal bottom sheet…`（前端） |
| 隐藏父分组下的项目不可聚焦 | `_accountMenuFocusable`（449–452）+ `_accountMenuHiddenAncestor`（442–447）跳过 `hidden` 祖先；`closeAccountMenu` 后内容不再进入焦点顺序 | `an entry inside a hidden group is not focusable…` |
| 目标页成功聚焦 | `_focusPersonalTarget`（2073–2086）：提交后聚焦目标页 `h2`/`[data-personal-body]`（补 `tabindex="-1"`），未渲染时 `setTimeout(…, 0)` 再试一次 | `an account entry navigates through the shared protected path…`（`focused === ['personal-agents']`） |
| 取消离页恢复焦点 | `openPersonalEntry` 先 `closeAccountMenu(true)`，焦点回到可见的 `#sidebar-account-toggle`；未提交则 `_focusPersonalTarget` 不执行 | `a cancelled leave keeps the original area…`（`closed === 1`、未提交、未同步标记） |
| 顶部菜单互斥 | `toggleAccountMenu` 打开前先隐藏 `#tenant-menu`；租户菜单打开时同理 | `account and tenant menus are mutually exclusive and repeated opening does not accumulate listeners` |

## 4. 生命周期与残留清理（4.4）

- `closeAccountMenu(returnFocus)`（558–573）是唯一收尾点：隐藏面板、`aria-expanded=false`、
  摘除 `pointerdown`/`focusin`/`keydown` 监听、把面板挂回 footer（`_mountAccountMenu(false)`）、
  `_accountMenuSheetChrome()` 收起遮罩与滚动锁，必要时把焦点交回触发器。
- 断点切换：既有 `window.addEventListener('resize', …)`（2267–2276）在缩放时
  `closeAccountMenu()`，并按新宽度收拢/恢复抽屉，因此放大到桌面不会留下 fixed 面板、遮罩或滚动锁。
- 认证失效/退出：`_invalidateAccountIdentity`（605–625）调用 `closeAccountMenu()` 与
  `_clearAccountPersonalState()`，见 3.x 证据。
- 主题与减少动态效果：面板颜色沿用账号菜单既有配色（深色面板 + `#363b39` 分隔），
  `console.css` 的 `@media (prefers-reduced-motion: reduce)` 关闭底部面板进入动画；
  `appearance.css` 已有全局 reduced-motion 段。

浏览器用例：`a breakpoint change closes the sheet and leaves no residue`（390→1440：面板节点回到
footer、遮罩隐藏、`body` 滚动锁解除）、`dark theme and reduced motion: the sheet still opens and
leaves no residue`（深色 + `prefers-reduced-motion: reduce`：面板背景非透明、`animation-name: none`、
关闭后无遮罩与滚动锁）。

## 5. 验证（命令与结果）

| 用例 | 命令 | 结果 |
| --- | --- | --- |
| 账号/面板前端契约 | `node --test tests/test_sidebar_account_frontend.cjs` | 64 用例，59 通过 / 5 失败（迁移前既有失败集合） |
| 真实浏览器契约 | `NODE_PATH=/tmp/cow-pw/node_modules PLAYWRIGHT_BROWSERS_PATH=/tmp/cow-pw/browsers node tests/test_personal_console_browser.cjs` | 24 场景通过，无 page error、无未预期请求 |
| 外观/主题前端契约（未受本 change 影响） | `node --test tests/test_appearance_frontend.cjs` | 29 用例，29 通过 |

Playwright 说明（环境，不属变更内容）：本机 `playwright` 包在 `/tmp/cow-pw/node_modules`，
浏览器构建在 `/tmp/cow-pw/browsers`，上述命令用 `NODE_PATH` + `PLAYWRIGHT_BROWSERS_PATH` 指向它们；
仓库内未安装 Playwright 时该文件仍可加载并跳过（`SKIP personal console browser contract`）。
