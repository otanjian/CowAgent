# 任务 2.1–2.4 — 账号面板宿主、触发器、区域顺序与三语文案

任务要求：把五个「我的」入口整体迁入账号菜单「我的资源」分组并移除主导航旧分组；账户触发器
改为头像 + 名称 + 展开箭头并补「个人资源与设置」提示与可访问名称；按身份 / 我的资源 / 账号设置 /
帮助与退出 / 品牌版本组织面板分隔且**不**新增个人顶部页签；补齐三语新增文案并沿用原翻译键。

## 1. 宿主迁移（2.1）

`channel/web/chat.html`：

- 旧分组已删除：文件内不再有 `menu-group [data-group="personal"]`，也不再有
  `data-view="personal-*"` 的 `.sidebar-item`（断言见第 5 节）。
- 新宿主在既有账号面板内，`#sidebar-account-footer`（409）→ `#sidebar-account-toggle`（410）
  → `#sidebar-account-menu`（426）→ `#account-menu-resources`（451），五个入口按原顺序平铺：

| 顺序 | 入口（`class="account-menu-action account-menu-item account-menu-personal"`） | 名称键 |
| --- | --- | --- |
| 1 | `data-view="personal-agents"`（459） | `menu_personal_agents`「我的智能体」 |
| 2 | `data-view="personal-channels"`（464） | `menu_personal_channels`「我的渠道」 |
| 3 | `data-view="personal-memory"`（469） | `menu_personal_memory`「我的记忆」 |
| 4 | `data-view="personal-tools"`（474） | `menu_personal_tools`「我的工具」 |
| 5 | `data-view="personal-skills"`（479） | `menu_personal_skills`「我的技能」 |

- 工作台与控制台共用同一份 DOM：面板只有一处（`#sidebar-account-menu`），窄屏时同一节点被
  移动到 `document.body` 做底部弹出（见 4.x 证据），**不**复制内容。
- 分组标题是标签而不是折叠器（`.account-menu-group-title`），入口本身直接可操作，未引入二级
  弹出菜单。

## 2. 账户触发器（2.2）

`channel/web/chat.html` 410–425：`#sidebar-account-toggle` 内为头像盘（`#sidebar-account-avatar`
/`#sidebar-account-avatar-icon`）、`#sidebar-account-name`、`#sidebar-account-subtitle`、区域状态
（`#sidebar-account-region`，屏幕阅读器文本，`aria-describedby`）与三点 chevron。

`console.js` `_renderSidebarAccount`（171–276，触发器部分 197–208）：

- `trigger.title` = 身份行（显示名 / 用户名）+「个人资源与设置」；
- 已登录且提示存在时 `trigger.setAttribute('aria-label', name + ' · ' + hint)`，未登录或提示缺失
  时移除该属性 —— 提示是补充，绝不替换账号名称；
- 显示名回退（`displayName || username`）、当前租户成员显示名、`@username` 副标题与图标侧栏
  触摸尺寸沿用原实现，未改动。

新增键（`channel/web/static/js/i18n/account.js`）：

| 键 | zh | zh-Hant | en |
| --- | --- | --- | --- |
| `account_menu_trigger_hint` | 个人资源与设置 | 個人資源與設定 | Personal resources and settings |
| `account_menu_resources` | 我的资源 | 我的資源 | My resources |
| `account_menu_settings` | 账号设置 | 帳號設定 | Account settings |
| `account_menu_title` | 账号 | 帳號 | Account |
| `account_menu_resources_checking` | 正在确认个人资源… | 正在確認個人資源… | Checking your personal resources… |
| `account_menu_resources_failed` | 个人资源暂不可用 | 個人資源暫不可用 | Personal resources are temporarily unavailable |
| `account_menu_resources_retry` | 重新检查 | 重新檢查 | Check again |
| `account_menu_region_personal` | 当前位于个人区域 | 目前位於個人區域 | You are in your personal area |

沿用未改的键：五个入口名称 `menu_personal_*` 与个人页所属组 `nav_group_personal`（「我的」），
`VIEW_META`（`console.js` 1617–1621）继续用它们作为面包屑组名/页名，五个 view ID、
`personal.*` 能力键与 `nav:personal.*` 授权编号均未改动。用户提交的身份与品牌文本走
`textContent`（不经 `innerHTML`），见 `_accountText` / `_renderSidebarAccount` 的既有写法和
`tests/test_sidebar_account_frontend.cjs`「database display is plain text…」。

## 3. 面板区域顺序与「不新增个人页签」（2.3）

`channel/web/chat.html` 的面板顺序（`id` 首次出现位置递增）：

```
account-menu-identity (434) → account-menu-resources (451) → account-menu-settings (485)
→ account-menu-about (504) → account-menu-logout (509) → sidebar-version (514)
```

分隔由 CSS 承担，不额外插入元素：`.account-menu-group { border-bottom: 1px solid #363b39 }`、
`.account-menu-version { border-top: 1px solid #363b39 }`（`console.css`）。

明确未实施优化细节 3：`chat.html` 无 `role="tablist"`、无 `id="personal-console-tabs"`、
无 `id="personal-shortcuts"`，五个个人页面本身未被本 change 修改。

## 4. 三语文案与解析安全（2.4）

- 新增键三语齐备（`i18n/account.js` 31–38 / 115–122 / 199–206），快照
  `tests/fixtures/console_i18n_snapshot.json` 同步（含此前缺失的五个 `menu_personal_*`）。
- 面板标题、关闭按钮、区域状态、检查中/失败/重试文案均通过 `data-i18n` 或 `t()` 输出；
  身份与品牌文本只经文本节点写入，不被当作 HTML 解析。

## 5. 验证（命令与结果）

| 用例 | 命令 | 结果 |
| --- | --- | --- |
| 账号/面板前端契约 | `node --test tests/test_sidebar_account_frontend.cjs` | 64 用例，59 通过 / 5 失败（失败集合与迁移前 HEAD 相同，见 1-3 与 6.3） |
| 个人页面前端契约 | `node --test tests/test_personal_console_frontend.cjs` | 57 用例，57 通过 |
| 三语键一致性 | `node --test tests/test_console_i18n_parity.cjs` | 5 用例，5 通过 |
| 真实浏览器 | `NODE_PATH=/tmp/cow-pw/node_modules PLAYWRIGHT_BROWSERS_PATH=/tmp/cow-pw/browsers node tests/test_personal_console_browser.cjs` | 24 场景全部通过 |

新增/迁移的前端断言：

- 「the five personal entries live in the account menu, in their original order」：五个入口在
  `#account-menu-resources` 内且顺序为 `personal-agents…personal-skills`，旧 `[data-group="personal"]`
  与 `#sidebar-nav` 内 `.sidebar-item[data-view^="personal-"]` 计数为 0，每个入口只有一个宿主。
- 「the account trigger names the account and its personal-resource hint」：触发器带
  `account_menu_trigger_hint` 的标题/可访问名称。
- 「the account panel keeps its region order and adds no personal page tab」：区域顺序、
  `role="tablist"` / 个人页签 / 页内快捷栏均不存在、两个分组标题各一次、资源组不是 `<details>`。

浏览器新增场景：`desktop: the account panel hosts all five personal pages in order`、
`the new account texts follow the selected language`、`zh-Hant: …`、`en: …`。
