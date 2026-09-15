# 任务 1.3 — 迁移前基线

任务要求：建立迁移前基线 —— 五个 view/能力/grant 编号、正常与部分授权菜单、工作台/控制台
进入个人页、个人深链接、当前项及未保存取消行为，明确需要迁移的旧 DOM 测试断言。

## 1. 迁移前的入口宿主（代码事实）

`channel/web/chat.html`：

- `#sidebar-nav`（`flex-1 min-h-0 overflow-y-auto`）内，工作台壳结束处有
  `<div class="menu-group open admin-menu-group" data-group="personal">`，
  组标题 `data-i18n="nav_group_personal"`（「我的」），组内五个
  `<a class="sidebar-item" data-view="personal-*">`。
- `#sidebar-account-footer` → `#sidebar-account-toggle`（头像 + 名称 + 三点 chevron）+
  `#sidebar-account-menu`（`role="group"`，`aria-labelledby="sidebar-account-name"`），
  菜单内当前只有身份区、状态/重试、`#account-menu-settings`（profile/password/prefs）、
  `#account-menu-about`、`#account-menu-logout`、`.account-menu-version`。

`console.js`：

- `VIEW_META` 五个个人条目 `console: 'personal.*'`、`group: 'nav_group_personal'`。
- `_applySidebarPermissions` 只扫描 `#sidebar-nav .sidebar-item[data-view]`，
  并用 `#sidebar-nav .menu-group[data-group="personal"]` 判定空组；匹配失败时只
  `classList.add('hidden')`，不整体重算（撤权后再授权无法恢复）。
- `navigateTo` 先做跨区域分支（写 pending + `_openNavArea`），再做可用性判定与离页检查；
  `_openNavArea` 内 `pushState` → `_applyNavAreaAttribute` → `_bootAreaDefaultView` 同步递归
  调用 `navigateTo`。
- 个人项当前项标记由 `navigateTo` 中 `querySelectorAll('.sidebar-item')` 循环写入
  `active` + `aria-current="page"`。

## 2. 迁移前的可观察行为基线（命令与结果）

| 验证 | 命令 | 结果 |
| --- | --- | --- |
| 账号/身份前端契约 | `node tests/test_sidebar_account_frontend.cjs` | 52 用例，47 通过 / 5 失败 |
| 个人页面前端契约 | `node tests/test_personal_console_frontend.cjs` | 56 用例，56 通过 |
| 个人页面真实浏览器契约 | `NODE_PATH=… node tests/test_personal_console_browser.cjs` | 11 场景全部通过 |

5 个失败用例与身份模式退役有关（`mode: 'legacy' / 'unknown'` 断言早于当前实现），
在本 change 开始前的 HEAD 上同样失败，已由依赖 change 的 9.1 证据记录；本 change 不修复、
不掩盖，实施后必须保持同一失败集合（见 9.x 交付证据）。

## 3. 需要迁移的旧 DOM 测试断言

| 文件 | 位置 | 旧断言 | 迁移方式 |
| --- | --- | --- | --- |
| `tests/test_personal_console_frontend.cjs` | 「the shell carries a personal sidebar group with all five entries」 | `html.includes('data-i18n="nav_group_personal"')` + `data-view="personal-*"` 在 shell 中 | 断言五个入口位于 `#sidebar-account-menu` 的「我的资源」组内，且 `#sidebar-nav` 不再包含个人条目 |
| 同上 | 「the personal entries are not marked as admin-only」 | 从 `data-view="personal-*"` 反查 `<a ` 标签 | 新宿主为账号菜单项，改为断言不带 `sidebar-hidden-admin-area` 与 `.sidebar-item` |
| `tests/test_personal_console_browser.cjs` | `openPersonalView` / 「desktop: the Mine group lists all five personal pages」 | `#sidebar [data-group="personal"]` 定位与顺序断言 | 改为「打开账户按钮 → 账号面板 `#account-menu-resources` 内定位」，保留顺序（我的智能体 … 我的技能）断言 |
| 同上 | 「narrow: the sidebar starts closed and the group is reachable」 | `#sidebar [data-group="personal"]` 可见 | 改为窄屏打开侧栏后账户入口可达、账号面板为底部弹出 |
| 同上 | 「a withdrawn capability hides its entry and names itself」 | `#sidebar [data-view="personal-memory"]` 隐藏、组内 4 项 | 改为账号面板内入口隐藏、资源组仍存在且可见项为 4 |

保留不迁移的断言：授权投影（`menu_denied` / `capability_disabled`）、拒绝页不启动消费者、
深链接、离页保护与请求计数。

## 4. 迁移前需固定的行为（不变量）

1. 五个 view ID、页面能力键、菜单授权编号、页标题「我的」前缀、`/chat#view-personal-*` 深链接。
2. 工作台与控制台从同一组入口进入同一批本人页面；控制台进入时切回工作台宿主且不改变租户。
3. 打开账号面板不预取五类个人资源、不创建会话、不启动渠道运行时。
4. 未授权目标（`menu_denied`）与能力关闭（`capability_disabled`）都不启动目标消费者。
5. 未保存内容取消离开时保留原区域、地址、草稿与当前项。
