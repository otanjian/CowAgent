# 任务 6.1–6.3 — 迁移、回滚、文档与交付校验

任务要求：核实无需数据库迁移、资源重建、授权补发或书签迁移，沿用项目静态资源版本机制匹配交付
HTML/JS/CSS 并演练只回退本 change 的前端差异；更新菜单使用说明与实施证据，逐项记录优化细节
1、2、4、5 的验收结果及细节 3 明确未实施，区分文档完成/代码完成/生产启用；完成严格 OpenSpec
校验、受影响测试与差异审查，按依赖增量顺序归档。

## 1. 无需迁移、补发或书签迁移（6.1）

| 项 | 事实 | 证据 |
| --- | --- | --- |
| 数据库迁移 | 无。本 change 未修改任何 `.py`、未新增表/列/索引 | `git status` 中本 change 触及的文件仅 `chat.html`、`console.js`、`console.css`、`i18n/account.js` 与测试/文档；`identity.db` 未变更 |
| 授权补发 / grant | 无。仍使用既有 `nav:personal.*` 菜单授权与 `personal.*` 页面能力键，未新增 capability、grant 或开关 | `evidence/3-1-authorization-navigation-current.md` 第 1 节 |
| 资源重建 | 无。个人资源归属仍是既有 `(tenant_id, user_id)` 业务域 | 同上；`tests/test_personal_console_transport.py` |
| 书签迁移 | 无需。五个 view ID 与 `/chat#view-personal-*` 深链接保持不变（只换宿主） | `VIEW_META`（`console.js` 1617–1621）未改；浏览器「desktop: a deep link opens the personal page directly」 |
| 新增静态文件 | 无。只编辑既有 `console.js` / `console.css` / `i18n/account.js` | `git status` |

静态资源版本机制（`channel/web/web_channel.py` 4127–4151）：`chat.html` 的 `assets/js/console.js`、
`assets/css/console.css` 会被注入 `?v=<cache_bust>`，且 `assets/js/i18n/*.js` 按目录发现后同样注入
（新增/修改的 i18n 命名空间无需改服务端清单）；`chat.html` 本身以 `no-store` 下发
（`channel/web/web_channel.py` 3403 等）。因此本 change 的 HTML/JS/CSS 三者会一起更新，不会出现
「新 HTML + 旧 console.js」的错配。

## 2. 回滚演练（只回退本 change 的前端差异）

回滚单元 = 本 change 交付的静态资源差异（`chat.html`、`console.js`、`console.css`、`i18n/account.js`）
+ 对应测试/快照；无任何持久化状态需要回滚。

演练与核对：

1. **交付前状态可独立运行**：在 HEAD 的独立 worktree 中运行交付前的 Node 契约
   （`git worktree add --detach /tmp/baseline-head HEAD`）：
   `test_sidebar_account_frontend.cjs` 41 用例 36 通过 / 5 失败（失败集合与本 change 交付后完全一致）、
   `test_session_history_frontend.cjs` 36 失败、`test_console_i18n_parity.cjs` 1 失败、
   `_tmp_repro_modeldefaults.cjs` 1 失败、`test_appearance_browser.cjs` 1 失败 —— 说明旧交付不依赖
   任何本 change 引入的状态。
2. **旧宿主只存在于被回退的文件里**：`data-group="personal"` 与 `data-view="personal-*"` 的旧标记
   在本仓库只出现在测试与 evidence 文本中（`rg -ln 'data-group="personal"' tests/ channel/` →
   仅 `tests/test_personal_console_browser.cjs` 的「应为 0」断言），即回退上述四个静态文件即可恢复旧
   宿主，不存在其他宿主副本需要同步清理。
3. **无后端耦合**：`rg -n "account-menu|account_menu" auth/ agent/ common/ config.py` 无结果，回滚
   前端不会留下悬空的服务端契约。
4. **旧宿主下功能保持**：旧交付自身的浏览器契约在依赖 change 的 `9-6-final-acceptance.md` 中记录为
   11 场景通过（个人入口在主侧栏时期），其页面、授权与请求计数断言与本次迁移后一致，仅入口定位不同。

回滚后需要保留的其他进行中改动：`chat.html` / `console.js` / `console.css` / 相关测试文件同时承载
`complete-database-capability-parity` 的改动（如路由可用性、记忆/日程控制台）。演练结论是"只回退本
change 的差异"，因此实际执行时应按 change 粒度回退而不是整文件 `git checkout`，以免覆盖对方成果。

## 3. 文档更新（6.2）

- `docs/design/menu-structure-audit-and-plan.md` 新增第 9 节「后续实施记录」：逐项列出优化细节 1、2、4、5
  的落地位置与证据，明确 **细节 3（个人页顶部页签/页内快捷导航/个人中心页）未实施**，并区分
  「文档完成 / 代码完成（工作区）/ 生产启用（未声明）」。
- `openspec/changes/move-personal-menu-to-account/proposal.md` 末行更新交付状态口径（原句仍称"仅为
  提案"，已改为提案+实现+证据、未归档、未声明生产启用）。
- 变更内的实施证据：`evidence/2-1-…`、`3-1-…`、`4-1-…`、`5-1-…`、本文件。

生产启用口径：本 change **不**改变任何开关默认值。成员个人控制台各切片的可启用结论沿用依赖
change `enable-member-personal-console` 的 `evidence/9-1-capability-switches.md` 与
`9-6-final-acceptance.md`（个人渠道执行切片仍为关闭、未通过）。

## 4. 严格校验与受影响测试（6.3）

| 校验 | 命令 | 结果 |
| --- | --- | --- |
| 本 change 严格校验 | `openspec validate move-personal-menu-to-account --strict` | `Change 'move-personal-menu-to-account' is valid` |
| 全仓严格校验 | `openspec validate --all --strict` | `Totals: 77 passed, 0 failed (77 items)`（总项数随其他进行中 change 的 spec 增删变动） |
| 账号/面板前端契约 | `node --test tests/test_sidebar_account_frontend.cjs` | 64 用例，59 通过 / 5 失败（与 HEAD 相同集合） |
| 个人页面前端契约 | `node --test tests/test_personal_console_frontend.cjs` | 57 / 57 |
| 三语键一致性 | `node --test tests/test_console_i18n_parity.cjs` | 5 / 5 |
| 共享导航切片 | `node --test tests/test_channel_scope_nav_frontend.cjs`、`tests/test_workbench_menu_grant_frontend.cjs`、`tests/test_nav_area_frontend.cjs`、`tests/test_console_view_registry.cjs` | 8 / 8、9 / 9、5 / 5、6 / 6 |
| 相邻账号/管理前端 | `tests/test_identity_admin_frontend.cjs`、`tests/test_appearance_frontend.cjs`、`tests/test_forced_password_gate.cjs`、`tests/test_external_identity_frontend.cjs`、`tests/test_console_workspace_frontend.cjs` | 27 / 27、29 / 29、4 / 4、26 / 26、5 / 5 |
| 真实浏览器契约 | `NODE_PATH=/tmp/cow-pw/node_modules PLAYWRIGHT_BROWSERS_PATH=/tmp/cow-pw/browsers node tests/test_personal_console_browser.cjs` | 24 场景通过，无 page error / 无未预期请求 |
| 个人/授权 Python 回归 | `.venv/bin/python -m pytest tests/test_personal_console_*.py tests/test_personal_capability_switches.py tests/test_personal_resource_config.py tests/test_personal_channel_console.py -q` | 246 通过 / 2 失败（见第 5 节） |
| 全量 Node 契约 | 逐文件 `node --test tests/*.cjs` | 通过集合 = 交付前通过集合 ∪ 本 change 新增用例；未新增失败 |

差异审查：本 change 的实现差异限定在四个静态资源 + 测试/文档；`chat.html` 的人工作业区
（`#sidebar-account-footer`、面板区域顺序、`#account-menu-backdrop`）与 `console.js` 的账号面板区段
（`_accountPanel`/`_renderAccountResources`/`_mountAccountMenu`/`openPersonalEntry`）都是自包含的，
跨区域导航顺序修正（离页检查先于提交）只影响 `navigateTo` 的判定顺序，不改变授权结果。

## 5. 未通过 / 不在本 change 范围（不得声明为已完成）

1. `tests/test_personal_console_menu.py::ZeroVisibilityRegressionTests` 两例：迁移前即失败，根因为内置
   role 菜单默认值未包含 `workbench.schedules`（既有无菜单 grant 的兼容规则让成员原本可读），属
   `enable-member-personal-console`/`complete-database-capability-parity` 的 Python 切片；本 change 未
   修改 Python 文件，不覆盖该实现，按依赖增量顺序在其 change 内处理。
2. `tests/test_sidebar_account_frontend.cjs` 的 5 例身份模式（legacy/unknown）失败：
   `evidence/1-3-premigration-baseline.md` 已记录其在 HEAD 上同样失败，属身份模式退役切片。
3. `tests/test_session_history_frontend.cjs` 36 例（沙箱缺 `queueMicrotask`）与
   `tests/_tmp_repro_modeldefaults.cjs`、`tests/test_appearance_browser.cjs`（缺 Playwright 环境）：
   HEAD 上同样失败，与本 change 无关。

归档顺序：本 change 依赖 `enable-member-personal-console`（其任务完成但尚未归档）与进行中的
`complete-database-capability-parity` 的共享文件成果。归档时按依赖增量顺序处理，只归档本 change 的
delta spec 与证据，不覆盖其他进行中 change 的规格或实现。
