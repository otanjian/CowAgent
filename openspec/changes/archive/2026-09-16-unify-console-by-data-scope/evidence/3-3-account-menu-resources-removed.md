# 3.3 / 3.4 「我的资源」分组与五项个人入口删除

对应任务：

- 3.3 从聊天页、控制台及其共享桌面/移动账号菜单删除「我的资源」标题与五项入口，清理专属检查/重试、占位和多余分隔线。
- 3.4 保留身份、个人资料、账号安全、个人偏好、帮助与关于、退出和品牌版本；同步触发器账号设置提示、三语文案、键盘焦点、移动弹层及低高度滚动。

基线：工作区已合入 `move-personal-menu-to-account`（把五项入口搬进账号菜单）。本任务按用户要求撤销该决定，且不重新回到 `#sidebar-nav`——正式入口改为控制台业务页面。

## 变更内容

| 位置 | 变更 |
| --- | --- |
| `channel/web/chat.html` | 删除 `#account-menu-resources` 分组（标题、`-status`、`-retry`、五个 `.account-menu-personal` 入口）；删除触发器上的 `#sidebar-account-region` 与 `aria-describedby`；更新两处「我的资源」注释 |
| `console.js` | 删除 `ACCOUNT_PERSONAL_VIEWS`、`_isPersonalView`、`_accountPersonalEntries`、`_accountPersonalEntry`、`_accountProjectionPhase`、`_consolePageEntryState`、`_renderAccountResources`、`refreshAccountResources`、`_syncAccountPersonalCurrent`、`_clearAccountPersonalState`、`_focusPersonalTarget`、`openPersonalEntry`；`_initAccountMenuResources` 收敛为 `_initAccountMenuChrome`（仅保留移动弹层遮罩关闭）；`_syncAccountMenuScroll` 改为回到列表顶部；清理全部调用点 |
| `console.css` | 删除 `.account-menu-resources-status`、`.account-menu-personal`、`.account-menu-personal[aria-current]`、`.sidebar-account-footer.is-personal` |
| `i18n/account.js` | 三语删除 `account_menu_resources`、`account_menu_resources_checking`、`account_menu_resources_failed`、`account_menu_resources_retry`、`account_menu_region_personal`；`account_menu_trigger_hint` 三语由「个人资源与设置 / 個人資源與設定 / Personal resources and settings」改为「账号设置 / 帳號設定 / Account settings」 |
| `tests/fixtures/console_i18n_snapshot.json` | 同步上述键删除与取值变更 |

权威投影只剩一处：侧边栏的 `_applySidebarPermissions`（3.1 已落地）。账号面板不再持有第二份页面裁决，也不再因为打开或重绘而请求五类业务资源。

## 保留边界

- 账号菜单保留：身份区、`账号设置`（个人资料 / 账号安全 / 个人偏好）、帮助与关于、退出登录、品牌版本。
- 删除入口不删除任何资源、凭据或记忆；旧个人地址仍由 8.1 的转接负责。
- 面板重绘、语言切换、账号/租户切换都不能复活该分组：相关计算函数已不存在，HTML 中也没有承载节点。

## 验证

新增 `tests/test_account_menu_no_personal_resources.cjs`（7 项，全部通过）：分组与五项入口不存在、保留项与顺序、触发器提示改为账号设置、12 个专属符号在 `console.js` 中不再出现、状态/重试/样式残留为 0、重绘路径不再触发资源读取、三语键已删除。

同步修正的既有测试（均通过）：

- `tests/test_sidebar_account_frontend.cjs`：删除 5 个「我的资源」测试与 4 个 `openPersonalEntry` 适配器测试及相应夹具；焦点顺序测试改用保留的 `账号设置` 分组；区域顺序断言改为「identity → 账号设置 → 帮助/退出 → 版本」。
- `tests/test_personal_console_frontend.cjs`：五项入口断言改为「shell 中不存在任何宿主」；触发器断言改为账号设置提示、无 `sidebar-account-region`。
- `tests/test_channel_scope_nav_frontend.cjs`、`tests/test_admin_area_group_gating.cjs`：移除对已删除投影函数的桩，并改为断言侧边栏门控不再向账号面板转交裁决。
- `tests/test_personal_console_browser.cjs`：面板宿主场景改为断言「不存在入口」，页面场景改为按自身地址直达。

全量结果：`node --test "tests/**/*.cjs"` → 629 项，586 通过，43 失败。43 项为本次改动之前的既有失败（`test_session_history_frontend.cjs` 等），本次未新增失败。

## 未完成/未验证边界

- **真实浏览器未验收**：本机无 Playwright/Chromium（仓库根无 `package.json`/`node_modules`），`tests/test_personal_console_browser.cjs` 自行跳过。该文件的改写只做了语法校验（`node --check`）与静态断言审阅，**未在真实浏览器执行**；真实双角色共用页面、直达/返回/离页取消、空态/故障、刷新与账号/租户切换属任务 3.6，尚未验收。
- `3.2`（普通用户接入共用页面本体）与 `3.5`（概览数据源按范围下发）未在本轮实施。
- 附带修复：`tests/fixtures/console_i18n_snapshot.json` 缺少在途工作新增的 `agents_set_default_private` 三语取值，导致 `tests/test_console_i18n_parity.cjs` 2 项长期失败（在 `HEAD` 上通过，属未提交工作造成的漂移）。本次补齐该键，该文件恢复 5/5 通过。
