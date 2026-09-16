# 任务 5.1–5.5 — 行为回归与启用门槛（G1 → G2）

任务要求：更新账号/个人页面前端与浏览器测试的入口定位并保留行为断言；验证真实菜单操作路径与
「只展开面板无个人业务请求」；跑个人菜单/授权回归（普通成员、租户管理员、平台管理员、部分撤权
与恢复、无租户、强制改密、投影失败、换账号晚到）；验收既有 feature flag 组合且 classic/split
不产生重复入口；在真实浏览器覆盖桌面/窄屏/短视口/键盘/三语/浅深主题。

交付状态口径：本文件记录的是**代码完成 + 本机可复现证据**。生产启用状态见 6.2，依赖
`enable-member-personal-console` 的切片启用结论，本 change 不改变任何开关默认值。

## 1. 测试宿主迁移（5.1）

| 文件 | 迁移内容 |
| --- | --- |
| `tests/test_sidebar_account_frontend.cjs` | 新增「我的资源」账号面板切片（`mountAccountResources`、`projectPages`、`personalEntry`、`accountPersonalNav` 等），把旧 `#sidebar-nav` 定位改为账号面板定位；新增 13 个用例（见第 2 节），既有身份/授权断言全部保留 |
| `tests/test_personal_console_frontend.cjs` | 「the shell carries a personal sidebar group…」改为「the five personal entries live in the account menu, in their original order」，断言 `#sidebar-account-menu` 内的新宿主与旧宿主计数为 0，页面契约（view ID、能力键、页标题）不变 |
| `tests/test_personal_console_browser.cjs` | `openPersonalView` 改为「窄屏先开抽屉 → 开账号面板 → 点面板内入口」；场景 1 改为面板内顺序/唯一宿主断言；窄屏场景改为底部面板模态语义与无残留；撤权场景改为面板内入口隐藏；新增 12 个场景 |
| `tests/test_channel_scope_nav_frontend.cjs` | 该切片只执行 `_applySidebarPermissions`，补 `_renderAccountResources`/`_syncAccountPersonalCurrent` 观测桩，并新增「the account panel recomputes the personal entries from the same projection」固定「一份投影、两个宿主」 |
| `tests/test_workbench_menu_grant_frontend.cjs` | 同上补两个桩（该切片不装载账号面板区段） |

未改动：`tests/fixtures/console_i18n_snapshot.json` 之外无快照；`tests/test_console_i18n_parity.cjs`
5 用例全通过（快照补齐新增键与五个 `menu_personal_*`）。

## 2. 新增/迁移的前端用例（`test_sidebar_account_frontend.cjs`，64 用例）

`the five personal entries live in the account menu, in their original order`、
`the account trigger names the account and its personal-resource hint`、
`the account 「我的资源」 group is recomputed from the authoritative projection`、
`an unconfirmed projection offers no activatable entry and no personal request`、
`an identity without a confirmed tenant offers no personal entry either`、
`a personal page carries exactly one current marker, on its account entry`、
`an account or tenant change clears the entries, the marker and the cached projection`、
`the mobile account panel is a modal bottom sheet outside the transformed sidebar`、
`an entry inside a hidden group is not focusable, and the panel is sized to the room it has`、
`the account panel keeps its region order and adds no personal page tab`、
`an account entry navigates through the shared protected path, checking the leave once`、
`a cancelled leave keeps the original area, page, address and current item`、
`a denied target renders the denial without starting its consumer`、
`a hidden account entry never navigates, and click/Enter/Space activate the visible ones`。

行为断言（授权投影、拒绝页不启动消费者、离页保护、请求计数、身份/租户边界）全部保留；导航类断言
直接执行 `console.js` 中 `// === ACCOUNT_PERSONAL_NAV_BEGIN/END ===` 标记块，避免断言副本。

## 3. 真实操作路径与「无预加载」（5.2）

浏览器契约新增 `desktop: opening the panel preloads no personal page`：以展开面板前后的请求计数差
断言 `/api/memory/personal`、`/api/personal/channels`、`/api/personal/resources` 与
`/api/agents?view=personal` 均未被触发。既有的五个页面渲染、深链接（`#view-personal-memory`）、
搜索过滤、窄屏可用性与「拒绝页不启动消费者」场景保留并改到新宿主。

## 4. 授权回归（5.3）

| 范围 | 用例 |
| --- | --- |
| 身份与角色 | `tests/test_personal_console_multi_tenant_authorization.py`、`tests/test_personal_console_menu.py`、`tests/test_identity_admin_frontend.cjs`（平台/租户管理员入口，27 通过）、`tests/test_sidebar_account_frontend.cjs`（普通成员、多租户、无租户、强制改密） |
| 撤权与恢复 | 前端「recomputed from the authoritative projection」+ 浏览器「with the personal console off the empty group is removed, actions stay」「a withdrawn capability hides its entry and names itself」 |
| 投影失败 | 浏览器「a failed projection offers a retry, never an unconfirmed entry」+ 前端「an unconfirmed projection offers no activatable entry and no personal request」 |
| 受限身份（无已选租户/未登录） | 前端「an identity without a confirmed tenant offers no personal entry either」；数据库模式无租户时与投影未确认同解（无可激活入口、无个人请求），租户选择器与强制改密门保持既有行为 |
| 换账号/租户晚到结果 | 前端「an account or tenant change clears the entries, the marker and the cached projection」 |
| 只通过账号入口不改 owner/成员边界 | 浏览器场景全程以普通成员（`personal-member`）身份访问 `/api/memory/personal` 等本人端点，请求内不含跨主体参数；`tests/test_personal_console_transport.py` 覆盖服务端边界 |

命令与结果：

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_personal_console_*.py tests/test_personal_capability_switches.py tests/test_personal_resource_config.py tests/test_personal_channel_console.py -q` | 246 通过 / 2 失败（失败属依赖 change 的 `auth/service.py` 菜单默认值切片，见第 7 节） |
| `node --test tests/test_identity_admin_frontend.cjs` | 27 通过 / 0 失败 |
| `node --test tests/test_sidebar_account_frontend.cjs` | 59 通过 / 5 失败（迁移前既有失败集合） |

## 5. feature flag 组合与不重复入口（5.4）

| 组合 | 期望 | 证据 |
| --- | --- | --- |
| 个人控制台开 | 五个入口在账号面板内可用 | 浏览器场景 1、`test_personal_capability_switches.py` |
| 五个个人页全部能力关闭 | 「我的资源」组整体消失（空组移除），账号设置/退出仍可用 | 浏览器「with the personal console off the empty group is removed, actions stay」 |
| 单个能力关闭（如记忆写入） | 该入口隐藏、其余 4 项保留、直接链接页面自述能力关闭且不启动消费者 | 浏览器「a withdrawn capability hides its entry and names itself」 |
| 菜单撤权（`menu_denied`） | 入口隐藏且拒绝页不启动消费者 | 浏览器「a denied page is refused by the shell and starts nothing」 |
| classic / split 呈现 | 不产生第二个宿主或重复入口 | 浏览器「split navigation duplicates no personal entry」 |
| 其他工作台/管理菜单 | 不受迁移牵连 | `test_workbench_menu_grant_frontend.cjs`（9 通过）、`test_channel_scope_nav_frontend.cjs`（8 通过）、`test_nav_area_frontend.cjs`（5 通过）、`test_console_view_registry.cjs`（6 通过） |

本 change **未**新增布局专用开关：可用的开关仍是依赖 change 的既有布尔开关，本次只改变入口宿主。

## 6. 真实浏览器验收（5.5）

命令：

```
NODE_PATH=/tmp/cow-pw/node_modules PLAYWRIGHT_BROWSERS_PATH=/tmp/cow-pw/browsers \
  node tests/test_personal_console_browser.cjs
→ personal console browser contract: 24 scenarios passed
  artifact: <tmp>/results.json（fixtureOnly: true，无 page error、无未预期请求）
```

覆盖矩阵（24 场景，均以真实 `channel/web/chat.html` + CSS + JS 运行，仅后端响应为 fixture）：

| 维度 | 场景 |
| --- | --- |
| 桌面（1440×900） | 面板内五个入口与顺序、唯一宿主、开面板不预加载、五个页面各自渲染、状态/动词/搜索、深链接、当前项唯一与重开可见、Escape/Tab 离开/外部点击、投影失败重试 |
| 控制台入口（`/admin`，租户管理员） | 从控制台账号面板进入个人页落到既有工作台宿主（`/chat`）、当前项正确、消费者只启动一次、其他个人消费者不启动 |
| 图标侧栏与短视口（1440×420） | 限高跟随账号卡上方空间、内部滚动、末项可达 |
| 窄屏（390×844） | 面板为 body 下的模态底部面板（`role="dialog"` + `aria-modal` + 遮罩 + 滚动锁）、选择后无面板/遮罩/抽屉残留、页面本身仍可用 |
| 断点切换 | 390 → 1440 关闭面板且无残留 |
| 键盘 | Escape 关闭并回焦触发器、Tab 越过末项关闭桌面弹层、外部点击关闭 |
| 三语 | zh / zh-Hant / en 的分组标题与触发器可访问名称 |
| 主题与动态效果 | 深色（`cow_theme=dark`、`cow_web_palette=classic`）+ `prefers-reduced-motion: reduce`：面板正常开关、背景非透明、无进入动画、无残留 |
| 能力/菜单授权 | 撤权、能力关闭、拒绝页不启动消费者 |

视觉证据：`results.json` 逐场景记录 `name/passed/durationMs`；截图可按需在 `COW_PERSONAL_BROWSER_OUTPUT`
指定的目录重跑（本机以 `results.json` 为准，未另存截图）。

模拟投影仅用于对应前端切片验证；依赖个人页面的真实授权结论仍以
`enable-member-personal-console` 的 9.x 证据为准（本 change 不重述、不改写）。

## 7. 已知失败集合（迁移前既有，非本 change 引入）

| 用例 | 数量 | 迁移前状态 | 归因 |
| --- | --- | --- | --- |
| `tests/test_sidebar_account_frontend.cjs`（身份模式 legacy/unknown 相关 5 例） | 5 | HEAD 上同样失败（`evidence/1-3-premigration-baseline.md` 记录） | 身份模式退役切片，属依赖 change |
| `tests/test_personal_console_menu.py::ZeroVisibilityRegressionTests`（2 例） | 2 | 本 change 开始前即失败 | 内置 role 菜单默认值缺 `workbench.schedules`；涉及 `auth/service.py` / `auth/store.py`，本 change 未修改任何 Python 文件 |
| `tests/test_session_history_frontend.cjs`（36 例，`queueMicrotask is not defined`） | 36 | HEAD 上同样失败 | 测试沙箱缺全局，与本次前端改动无关 |
| `tests/_tmp_repro_modeldefaults.cjs`、`tests/test_appearance_browser.cjs` | 各 1 | HEAD 上同样失败 | 临时复现脚本与缺 Playwright 环境 |

本 change 交付前后「通过集合严格包含原通过集合」，未新增任何失败。
