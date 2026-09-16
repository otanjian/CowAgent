# 8.8 组件面退役（强制收口，残留待修）

**状态：用户决定强制收口并归档，残留项随后修复。本文件由收口方（非原执行 worker）在停止该 worker 后依实测树状态补写，故「已核」与「未核」严格分列。**

## 1. 为什么必须做

`specs/member-personal-console/spec.md` 的 ADDED 要求（`spec.md:7`）写明旧个人功能「不得继续维护独立页面、表单或业务实现」。归档会把该文本合并进 `openspec/specs/`（本仓库的需求权威），故须先使其成立或明确记录不成立范围。

## 2. 已退役（已核）

| 项 | 证据 |
|---|---|
| 独立页面模块 | `channel/web/static/js/personal-console.js` 已删除（`ls` 报 No such file；原 808 行、含 `personal-agents`/`personal-channels`/`personal-memory` 三视图） |
| 页面装载 | `chat.html` 中该模块的 `<script>` 已移除（`grep -c personal-console.js` → 1，仅剩 i18n 行，见 §4 残留③） |
| 五个 `personal.*` 页面**签发** | `PERSONAL_CONSOLE_PAGES` 在 `auth/service.py` 中已无定义（`grep` 空），原 `:117-121`、`:174-187` 与 `:3451` 签发循环一并移除 |
| 外部调用点 | `PersonalConsole.invalidatePersonalViews` 全仓库 JS 零命中（原 `console.js:1835-1837`、`identity-admin.js:35-37` 已清） |
| 路由基线 | `scripts/route-baseline.txt` 已重算（文件被修改，注释记 128 patterns / 166 method entries、0 mismatch） |

## 3. 有意保留（关键判定，**与原 8.8 文本相反**）

**四个薄适配旧端点仍注册**：`route_registry.py:200-203`（`/api/memory/personal`、`/api/memory/personal/content`、`/api/personal/channels`、`/api/personal/channels/([^/]+)`）。

原 8.8 把它们列为「待退役」，**该框架是错的**。依据 `specs/unified-console-access/spec.md:53`「旧地址在一个兼容发布周期内仅作保持原目标与归属约束的转接，不保留独立业务实现」——要求退役的是**独立业务实现**，而转接本身被要求保留；同文件场景 `:59-61` 更明文规定：

> **WHEN** 兼容期内客户端通过旧接口提交合法本人操作 → **THEN** 请求由统一服务处理，使用同一版本和授权规则，结果保持原响应兼容

故退役这些端点会**破坏本 change 自身的 delta 场景**。它们转发到统一服务，属规范要求的转接而非独立实现，予以保留。这也使 8.2 的旧入口实时清单需要重读（清单把端点记为「仍注册」是事实，但「应退役」的结论不成立）。

## 4. 关键不变量（已核）

- **grant 规范化未随之退役**：`canonical_menu_id()` 仍在（`auth/service.py:124`），仍由 `:3324` 用于规范化既有 grant 的 `resource_id`，`LEGACY_PERSONAL_MENU_MAP`（`auth/policy.py:550`）与 `_migration_26`（`auth/store.py:1358-1393`）未动。**「停止签发页面」与「继续规范化旧授权」是两件事**——这是本次退役最可能误伤真实用户既有授权的点，已确认未混同。**限代码路径核对，未跑断言 8.8 的端到端用例。**
- **数据与凭据未删、未转共享**：退役只作用于 JS 模块、`chat.html` 装载行与页面签发；未触碰业务行与凭据存储。8.2 已记录升级只删 `role_resource_grants` 菜单行。
- **8.1 地址转接仍在**：`legacyPersonalForward`（`console.js:1826`）仍在 `navigateTo`（`:1846`）中被调用，且仍先于授权门与离页检查。

## 5. 测试：改写为新契约，非删除（已核）

- `tests/test_channel_workbench_frontend.cjs:405` → 断言 `chat.html` **不再**加载 `assets/js/personal-console.js`。
- `tests/test_personal_address_forward_frontend.cjs:135,139` → 断言 `invalidatePersonalViews` **不存在**。该断言原守 8.1 的迟到响应隔离；因唯一会发起个人取数的模块已退役，全仓库 JS 已无 `api/personal`/`view=personal` 取数点，故该隔离**已无主体**（不是被削弱，而是不可测），而「转接先于授权门与离页检查」仍由同文件 `:110-125` 覆盖。

## 6. 实测（含命令与条件）

- `NODE_PATH="$(npm root -g)" node --test tests/test_console_i18n_parity.cjs` → **5 passed / 0 failed**（键级快照仍一致）。
- `NODE_PATH="$(npm root -g)" node --test tests/*.cjs` → **45 失败 / 611 通过**，对照文档化基线 **44**（`evidence/8-4b-*`／8.9），**差 1**。失败分布：36 `test_session_history_frontend` ＋ 5 `test_sidebar_account_frontend` ＋ 1 `_tmp_repro_modeldefaults` ＋ 2 浏览器文件（既有）＋ **1 `test_personal_console_frontend`（本次新增，见 §7①）**。
  - 注：8.8 执行中途一次取样为 48，其中 4 条为**当时正在改写**的中间态（`test_channel_workbench_frontend`、`test_recovered_pages_frontend`、`test_personal_console_frontend`、`test_personal_address_forward_frontend`）；改写落地后回落到 45。故该中间态不是回归，但**「44」这一基线数字此后应读作 44＋本次 1 条新增**。
- 全部改动过的 Python 文件 `ast.parse` 通过；`channel/web/static/js/**/*.js` 全部 `node --check` 通过（停止 worker 后核，无半写文件）。

## 7. 残留（随「后续修复」，均未收口）

1. **`tests/test_personal_console_frontend.cjs` 失败**：它是被退役模块的测试文件，尚未改写为新契约或退役。这是 §6 中 45 vs 44 的唯一新增。
2. **`tests/test_personal_console_browser.cjs` 有 1 个场景未完成**：worker 停止时自述 `a failed projection leaves the panel usable and offers no resource retry` 仍失败于 `#account-menu-retry` 未变为可见。该文件同时是任务 5.4「退役地址转接不重绘」断言的宿主，故 5.4 的浏览器断言仍无法在该宿主内通过。
3. **死的 i18n 文件**：`channel/web/static/js/i18n/personal-console.js` 仍被 `chat.html:2803` 加载（26 键），但其消费者（唯一使用方）已删除。功能无害（仅多载 2.4KB 未被读取的字符串），属清洁度残留。移除须同时键级更新 `tests/fixtures/console_i18n_snapshot.json`，未做。
4. **8.8 的完整验收未做**：原计划的变异验证（重新引入模块/签发 → 新测试转红）、必需套件全跑、以及 route-baseline 重算与 `check-route-coverage.py` 的一致性复核（仅见基线文件已改及其自述 0 mismatch），均因停止 worker 而未由收口方复跑。
5. **grant 规范化未跑端到端断言**：§4 结论来自代码路径核对，非实测用例。

## 8. 判而未证

§3 对薄适配端点的保留判定基于 `unified-console-access:53/59-61` 的文本阅读。若后续认为「转接」只指地址层而不含 API 适配，则该判定需重审，届时 `${PERSONAL_*}` 四个端点与被 8.2 记录的五开关需一并重读。
