# Evidence: 租户编辑器统一保存与新建租户管理员账号

> 状态：**组 1～7、9、10 已完成并经测试验证；剩 4.11 / 8.3 两项「完整真实浏览器核对」未完成**。
> 已从真实浏览器取得真实链路证据：验收组 5 时的一次失败提交（根因已定位并修复，见 §11），以及强制改密门禁的可见性修复（§12，已在真实浏览器验证门禁可见可交互）。
> 仍未完成的是**一次成功的浏览器端到端「创建账号 + 统一保存」全流程目视核对**（§8.3 的模式切换与暗色样式部分）。
> 未完成项在 §9 明确列出，不在本文中作任何「已验收」表述。

## 0. 实现前基线：前端契约测试全量

采集时点：本 change 的后端改动（`auth/service.py`、`channel/web/admin_handlers.py`）**尚未**触及任何前端文件，因此该基线可视为前端侧的起点。

命令（逐文件运行，收集每文件的汇总行）：

```sh
for f in tests/*.cjs; do b=$(basename "$f"); [ "$b" = "_tmp_repro_modeldefaults.cjs" ] && continue; \
  node --test "$f" 2>&1 | rg "^(ℹ|#) (pass|fail) [0-9]+" | tr '\n' ' '; echo "$b | $r"; done
```

结果（18 个文件，排除 `_tmp_repro_modeldefaults.cjs`）：

| 文件 | pass | fail |
| --- | --- | --- |
| `branding_frontend.test.cjs` | 1 | **6** |
| `test_admin_home_frontend.cjs` | 2 | 0 |
| `test_agent_profile_frontend.cjs` | 4 | 0 |
| `test_agent_workbench_frontend.cjs` | 15 | 0 |
| `test_appearance_browser.cjs` | 0 | **1** |
| `test_appearance_frontend.cjs` | 29 | 0 |
| `test_composer_agents_frontend.cjs` | 5 | 0 |
| `test_identity_admin_frontend.cjs` | 13 | 0 |
| `test_nav_area_frontend.cjs` | 5 | 0 |
| `test_scene_workbench_frontend.cjs` | 3 | 0 |
| `test_scenes_frontend.cjs` | 6 | 0 |
| `test_scheduler_frontend.cjs` | 3 | 0 |
| `test_session_history_frontend.cjs` | — | — |
| `test_sidebar_account_frontend.cjs` | 41 | 0 |
| `test_tenant_admin_account_picker.cjs` | 9 | 0 |
| `test_tenant_create_frontend.cjs` | 4 | 0 |
| `test_tenant_tabbed_editor_frontend.cjs` | 22 | 0 |
| `test_todo_frontend.cjs` | 8 | 0 |

合计（不含 `test_session_history_frontend.cjs`）：**170 pass / 7 fail**（17 个文件有数值）。

> **关于基线的口径（重要，避免误读）**：上表是**本 change 动手之前**的起点。
> 另有一次完整扫描（18 个文件、`TOTAL pass=180 fail=7`，采集于组 7 用例迁移**进行中**，其 `test_tenant_tabbed_editor_frontend.cjs` 记为 32 项）曾被误当作基线引用；它实际是迁移途中的快照，**不是**基线。
> 正确的关系是：基线 `tabbed=22` →（组 7 迁移，+12）→ `34` →（组 5，+8）→ `42`。两条链的差集恰好等于 +20 pass / +0 fail，见 §7.4。

### 基线中的既存问题（非本 change 引入）

1. **`test_session_history_frontend.cjs` 运行后不退出。** 该文件的用例会逐条输出通过/失败结果，但进程在输出汇总行之前挂起，因此拿不到 `ℹ pass/fail` 汇总。已确认：
   - `node --test --test-force-exit` 无效（说明不是残留句柄导致，而是测试运行器仍在等待某个未结算的用例）；
   - shell 看门狗 20s 后强制结束才能继续。
   本 change 未改动该文件或其实验对象，属既有问题。它同时是「全量 `tests/*.cjs` 一次跑不完」的原因。

2. **`branding_frontend.test.cjs` 6 项失败、`test_appearance_browser.cjs` 1 项失败。** 同样非本 change 引入（本 change 此时尚未修改任何前端文件）。这两个文件不在本 change 的改动范围内，故仅在后续组中确认「失败数不增加」，不在此处修复。

以上两项均为工作树既有状态；若后续需要，应各自开独立 change 处理，不与本 change 混做。

## 1. 服务层：成员显示名可修改（组 1）

命令：

```sh
.venv/bin/python -m pytest tests/test_identity_service_writes.py -q
```

RED（实现前）：`3 failed, 1 passed`，失败原因为 `AssertionError: 'Root' != 'Root Renamed'`，即有效成员的显示名被静默忽略——证明该缺陷真实存在而非仅前端未接。

GREEN（实现后）：`20 passed`。

变异核验：将 `set_tenant_admin` 中 `tenant.create_admin` 之外的审计 action 改名后对应用例失败，说明审计断言非恒真。

## 2. 服务层：新建租户管理员账号（组 2）

命令：

```sh
.venv/bin/python -m pytest tests/test_identity_service_writes.py -q
```

RED（实现前）：`12 failed`，全部为 `AttributeError`（`create_tenant_admin_account` 不存在）。

GREEN（实现后）：`33 passed`。

变异核验：把新方法的审计 action 改为 `tenant.create_admin.MUTATED` 后，`test_creates_account_membership_admin_binding_and_audit` 失败（`1 failed`），确认审计断言可失败。

## 3. Handler 层：`mode` 分派（组 3）

命令：

```sh
.venv/bin/python -m pytest tests/test_identity_web_handlers.py -q
```

RED（实现前）：`4 failed, 3 passed, 64 deselected`（`-k admins`）。未知 `mode` 用例的失败信息为 `AssertionError: '200 OK' != '400'`，证明旧实现在收到未知 `mode` 时**静默按绑定路径放行**，未做校验。

GREEN（实现后）：`71 passed`。

### 过程中发现并修正的自伤

新增 handler 测试时误加了一个 `_bare_tenant` 辅助方法，**覆盖了测试类中同名的既有方法**（默认 `code` 由 `beta` 变为 `bare`），导致 4 个既有租户档案用例失败。仅运行新增用例无法发现；改为运行整个文件后暴露。已删除重复定义并复用既有辅助方法，恢复 `71 passed`。

## 4. 前端：统一保存与密码弹窗（组 4）

实现：`channel/web/static/js/identity-admin.js` 新增 `_tenantBatchSteps` / `_tenantBatchValidationError` / `_reportTenantBatchError` / `withTenantPassword` / `_openTenantPasswordModal`；`submitTenantEditor` 改为批量执行器，三个写入拆为 `_tenantSaveAdmin` / `_tenantSaveGrants` / `_tenantSaveBasic`；删除两个常驻 `tenant-fld-recent_password`、`tenant-fld-admin_recent_password` 输入。

命令：

```sh
node --test tests/test_tenant_tabbed_editor_frontend.cjs
```

RED（实现前）：`10 failed / 32`，失败集中在本组新增的 10 条断言。

GREEN（实现后）：`34 passed`（本文件含组 7 迁移后的既有用例）。

### 4.1 定点变异核验（对应任务 8.1）

每条变异只改一处，随后运行对应用例；`CAUGHT` 表示该断言确实可失败。

| 变异 | 结果 |
| --- | --- |
| `_tenantBatchSteps` 的执行顺序反向（`for (const step of [...pending].reverse())`） | **CAUGHT** |
| 资源授权步骤不再递增版本 | **CAUGHT** |
| 成功后不清除脏标记 | **CAUGHT** |
| 仅授权脏时也弹密码弹窗 | **CAUGHT** |
| 密码 401 不再原地重试 | **CAUGHT** |
| 失败信息不再指明步骤 | **CAUGHT** |
| 某步失败后继续执行后续步骤 | **CAUGHT** |
| Esc 不再等价于取消 | **CAUGHT** |
| 空密码被静默接受 | **CAUGHT** |
| 成功关闭弹窗时不清空密码输入 | **CAUGHT** |

**一次无效变异（记录以免误判）**：最初把 `submitTenantEditor` 中列出待保存标签的数组 `['admin','model','tool','basic']` 反序，结果用例未失败，一度看似「漏测」。核查后确认该变异**语义无效**——真正的执行顺序由 `_tenantBatchSteps` 内部固定决定，该数组只是「哪些标签脏」的集合。为避免后续读者（含自动化）重犯同样的误判，已把该数组改为就地字面量并加注释说明顺序归 `_tenantBatchSteps` 所有；随后改用「反转 `pending` 迭代顺序」这一真正影响执行顺序的变异，用例立即失败（见上表第一行）。

### 4.2 密码不再落盘

`the password is never persisted after a successful save` 断言保存成功后 `localStorage` / `sessionStorage` 的写入记录中不含该密码，且弹窗输入已清空。变异「成功关闭弹窗时不清空密码输入」可使其失败。

### 4.3 与 `apiFetch` 401 处理的关系

`apiFetch` 原先对任何 401 都抛 `new Error('unauthorized')` 并弹出登录浮层。用户在真机上遇到的「填错当前密码后页面显示 `unauthorized`」正是此路径：服务端返回 `401 invalid_old`，前端把真实原因替换成了 `unauthorized`，看起来像会话失效。

处置：401 错误对象现在带上 `status` / `code`，新增 `suppressAuthOverlay` 选项（在密码弹窗发起的写入上启用），使密码输错表现为弹窗内的 `tenant_password_wrong` 可重试提示，而不是登录浮层。**未改动其他视图的 401 行为**（`test_identity_admin_frontend.cjs` 等 13 项仍通过），因此影响面受限于本 change 的写入路径。

## 5. 前端：租户管理标签双模式（组 5）

### 5.1 实现内容

`channel/web/static/js/identity-admin.js`：

- `#tenant-panel-admin` 内新增两个**常驻**面板与模式切换：`#tenant-admin-body-existing`（含既有 `#tenant-admin-picker` 与成员显示名输入 `#tenant-fld-admin_display`）与 `#tenant-admin-body-new`（`#tenant-fld-admin_new_username` / `_display` / `_password`）。切换只改 `hidden` 与 `.active`，**不重建 DOM、不清空输入**，因此「切换模式不丢另一种模式的内容」由结构本身保证，而非靠额外保存/恢复逻辑。
- `setTenantAdminMode(mode)`、`_tenantAdminBody(pw)`、`_resetTenantAdminFields()`、`_tenantAdminField(id)`。
- 提交体按模式组装，与 handler 契约（design §4.4）逐字段对齐：

| 模式 | 请求体 |
| --- | --- |
| `existing` | `mode=existing`、`user_id`（来自 `_userPickerState`）、`display_name`、`recent_password` |
| `new` | `mode=new`、`username`、`display_name`、`temporary_password`、`recent_password` |

  两种模式都不发对方专有字段（5.4 / 5.5 各有一条断言专守这一点）。
- 校验 `_tenantBatchValidationError` 按模式分支，逐字段给出可操作提示（缺哪个报哪个）：`tenant_admin_new_username_required` / `_display_required` / `_password_required`；选择模式沿用 `admin_user_picker_required`。
- 选中账号后以该账号显示名**预填**成员显示名且保持可改：`_loadUserPickerCandidates` 为每个候选按钮新增 `data-user-name`（选项文本是「显示名 + 用户名」两行，`textContent` 不能直接当显示名用），点击时经 `onSelect(state)` 回传 `displayName`。
- 新建成功后调用 `_refreshUserPicker` 重查候选集。新增 `_userPickerRegistry` 记录已渲染 picker 的 node+state，使重查**保留已选 id**，不因刷新而丢掉操作者已做的选择。
- 保存失败时在错误对象上附带 `stepKey` / `stepAdminMode`（由 `_tenantBatchSteps` 在构造步骤时记录），使「管理员步骤 + `new` 模式 + 409」能精确解释为**用户名已被占用**并提示可改用「选择已有账号」，而不是笼统的「失败步骤：租户管理（版本冲突）」。

### 5.2 测试与红绿

`tests/test_tenant_tabbed_editor_frontend.cjs` 新增 8 项（该文件 34 → 42 项）。

RED：实现前该 8 项全部失败，失败原因是「找不到模式切换元素 / 请求体不是按模式组装」（`TypeError: Cannot read properties of null (reading 'dispatch')`、`AssertionError: both ways of appointing an admin are offered`），原生 34 项仍全绿——即失败确实来自缺失的功能，而非测试自身写错。

```sh
node --test tests/test_tenant_tabbed_editor_frontend.cjs
```

GREEN：

```
ℹ tests 42
ℹ pass 42
ℹ fail 0
```

### 5.3 定点变异核验（对应任务 8.1）

对组 5 的每条新断言做点变异，逐个确认「断言真的会因为它所声称的行为被破坏而失败」。脚本 `/tmp/mut_group5.cjs` 对 `identity-admin.js` 做精确文本替换后运行该测试文件，`fail > 0` 记为 `CAUGHT`：

| 变异 | 结果 |
| --- | --- |
| 缺省模式改为 `new` | CAUGHT |
| `_tenantAdminBody` 永不组装 `mode=new` | CAUGHT |
| 去掉「显示名必填」校验 | CAUGHT |
| 选中账号后不再预填显示名 | CAUGHT |
| 切回选择模式时清空新建表单 | CAUGHT |
| 409 回退为笼统的步骤失败提示 | CAUGHT |
| 新建成功后不再重查候选集 | CAUGHT |
| 选择模式不再显式声明 `mode=existing` | CAUGHT |
| 选择模式发送空的 `user_id` | CAUGHT |

```
caught 9/9
```

> 说明：其中「切回选择模式时清空新建表单」这一条是**专为 5.6 设计**的变异——它只破坏「切换不丢内容」这一条不变量，因此只有 5.6 会失败，证明 5.6 不是被别的断言的副作用顺带覆盖的。

### 5.4 与既有行为的关系

- `_userPickerState` 原先在编辑器重新打开时不清理，上一次编辑的租户所选账号会残留到下一个租户的表单里（既有缺陷，组 5 的「选择模式」提交体直接读取该全局值，会把它带到写请求）。本次在 `_resetTenantAdminFields` 中一并清空，并在编辑器打开时调用。
- `onSelect` 由 `state.onSelect()` 改为 `state.onSelect(state)`。既有调用方 `openAdminModal` 不传 `onSelect`（走 `markModalDirty()`），不受影响；`tests/test_identity_admin_frontend.cjs` 13 项全绿。


## 6. 样式与文案（组 6）

- **6.1** `channel/web/static/css/console.css` 新增 `.tenant-password-modal` 规则：`z-index: 80`（高于全页编辑器的 20）、标题/底部间距、输入框占满宽度。组 5 补充模式切换样式：`.tenant-admin-modes`、`.tenant-admin-mode`、`.tenant-admin-mode.active` 及各自 dark 变体（沿用既有 `role-editor-*` 的配色与圆角）。
- **6.2** `channel/web/static/js/console.js` 的 `zh` / `zh-Hant` / `en` 三套字典补齐 14 个密钥：密码弹窗与批量保存 5 个（`tenant_password_title`、`tenant_password_hint`、`tenant_password_required`、`tenant_password_wrong`、`tenant_editor_save_failed_step`）、双模式 9 个（`tenant_admin_mode_existing`、`tenant_admin_mode_new`、`tenant_admin_new_username`、`tenant_admin_new_display`、`tenant_admin_new_password`、`tenant_admin_new_username_required`、`tenant_admin_new_display_required`、`tenant_admin_new_password_required`、`tenant_admin_username_taken`）。核对：每个密钥在文件中各出现 3 次。字段标签与模式标签经 `_localizeTenantEditorChrome` 的 `labels` / `modes` 映射注入。
- **6.3** 新增 `tests/test_i18n_tenant_editor_keys.cjs`（4 项），组 5 的 9 个新键已加入其 `NEW_KEYS` 显式清单。

### 6.3 的实现说明与变异核验

不能对 `console.js` 用正则直接抓字典：`I18N` 由一个含函数与嵌套对象的巨大字面量构造，之后又被 6 处 `Object.assign(I18N.<lang>, {...})` 追加；而整体放进沙箱执行会在文件后段因引用浏览器外不存在的模块而抛错，且**抛错点早于后 3 处 `Object.assign`**，用「容忍加载失败」的写法会静默漏掉后半部分字典。因此该测试改为对大括号配平扫描定位各语言区块后逐语言归集键名。

命令：

```sh
node --test tests/test_i18n_tenant_editor_keys.cjs
```

GREEN：`4 passed`。

变异核验：删除 `en` 字典中的 `tenant_password_wrong` 后，测试以精确信息失败：

```
AssertionError: tenant_password_wrong is missing from the en dictionary
```

同一变异下 `the tenant editor key set is identical across languages` 亦失败（`2 failed / 4`），证明「漏译」既被逐键断言捕获、也被跨语言一致性断言捕获。组 5 的键同样核验：从 `en` 字典删除 `tenant_admin_mode_new` 后，逐键断言以 `tenant_admin_mode_new is missing from the en dictionary` 失败，跨语言一致性断言亦失败（`2 failed`），恢复后 `4 passed`。另有一项 `the dictionary scanner is not silently reading empty blocks` 专门守住扫描器本身，避免上述断言因抓不到区块而恒真。

## 7. 既有用例迁移（组 7）

| 任务 | 处理 |
| --- | --- |
| 7.1「按标签独立保存」 | `the model and tool tabs save independently without clobbering each other` 经复核**仍然成立**：它执行的是两次独立保存（各自一次 PUT），第二次仍需携带另一 kind 的授权。保留原断言（`puts.length === 2`、版本 4 → 5），仅据新契约校正措辞。 |
| 7.2 面板内 `recent_password` 字段 | `the panels no longer carry a standing current-password field` 改为断言 `tenant-fld-recent_password` 与 `tenant-fld-admin_recent_password` 均为 `null`；`test_tenant_create_frontend.cjs` 的 `the create editor collects only code/name, with the password collected at save time` 断言密码不再出现在创建表单字段中。 |
| 7.3 两个既有前端文件 | `test_tenant_create_frontend.cjs`（4 项）与 `test_tenant_admin_account_picker.cjs`（9 项）的提交助手改为「触发保存 → 在弹窗中确认密码」，并先派发 `input` / `change` 使标签进入脏集合。断言「未选账号不得提交」的两个用例改为先把该标签标脏，以保留其原意（不因统一保存的「无脏标记即空操作」而变成恒真）。 |
| 7.4 全量 | 见下。 |

### 7.4 全量 `tests/*.cjs`

采集脚本：`/tmp/sweep.cjs`（对每个文件用 `spawnSync` 的 `timeout` 独立限时，避免 shell 看门狗把「运行失败」与「超时」混为一谈）。

```sh
REPO=$PWD SWEEP_TIMEOUT_MS=90000 node /tmp/sweep.cjs
```

结果（与 §0 的口径一致：排除跑完不退出的 `test_session_history_frontend.cjs`，以及仓库内既有的临时脚本 `_tmp_repro_modeldefaults.cjs`）：

```
files        17
TOTAL        pass=190 fail=7
新增文件     test_i18n_tenant_editor_keys.cjs（4 pass / 0 fail，不在基线集合内）
```

与基线的逐文件对照：

| 文件 | 基线（§0） | 现在 | 差 |
| --- | --- | --- | --- |
| `test_tenant_tabbed_editor_frontend.cjs` | 22 | **42** | **+20** |
| `test_tenant_create_frontend.cjs` | 4 | 4 | 0 |
| `test_tenant_admin_account_picker.cjs` | 9 | 9 | 0 |
| `test_identity_admin_frontend.cjs` | 13 | 13 | 0 |
| `test_sidebar_account_frontend.cjs` | 41 | 41 | 0 |
| `test_nav_area_frontend.cjs` | 5 | 5 | 0 |
| 其余 11 个文件 | — | 与基线逐一相同 | 0 |
| **合计** | **170 / 7** | **190 / 7** | **+20 / +0** |

`+20` 的构成可**精确闭合**：组 7 把既有用例迁入标签编辑器文件（22 → 34，+12），组 5 新增双模式用例（34 → 42，+8）。

结论：**失败数与失败文件与基线完全一致（各 7 例：`branding_frontend.test.cjs` 6、`test_appearance_browser.cjs` 1），通过数 +20 且完全可归因，未引入回归。**

> 采集说明：本表为逐文件 `node --test` 实测，脚本 `/tmp/sweep2.cjs`；`test_session_history_frontend.cjs` 以单文件 90s 超时排除。
> 需注意 `/tmp` 不持久，故本表数字已完整落到本文中；重跑只为核对，不依赖脚本留存。
> 另修正一处**先前记录错误**：曾把一次「组 7 迁移进行中」的完整扫描（18 文件、`180/7`、其中标签编辑器为 32 项）当作基线，并据此得出一段「+2 无法归因」的说明。该快照实为中间态，非基线；已按 §0 的口径重算，归属完全闭合，先前的不可归因结论已作废。

## 8. 后端端到端与真实数据

### 8.1 端到端序列（对应任务 8.2）

原先以 `/tmp/e2e_batch_save.py` 驱动（`web.application` + 临时 `identity.db`，22/22 通过）。由于 `/tmp` 不持久，同样的序列已**固化为 pytest 用例**，作为长期回归保护而非一次性脚本：

```sh
.venv/bin/python -m pytest tests/test_identity_web_handlers.py -q -k "in_order or replaying or wrong_current or without_an_admin"
```

`tests/test_identity_web_handlers.py` 新增 4 项：

1. `test_admin_then_grants_then_profile_commits_in_order` —— 建租户 → 建号并绑定租户管理员 → 单次 PUT 同时覆盖 model/tool → `operation=profile` 启用；断言：管理员绑定**不**消耗租户版本、授权步骤恰好 +1、档案步骤恰好 +1（即版本串联）、响应不回显临时密码、审计含 `tenant.create_admin` / `tenant.resource_grants.set` / `tenant.set_profile`。
2. `test_creating_without_an_admin_leaves_a_tenant_no_profile_write_can_enable` —— 记录既有不一致（见 §8.3）。
3. `test_replaying_a_consumed_version_is_refused_and_changes_nothing` —— 复用已消耗版本返回 409 且不改动数据（对应界面 409 提示）。
4. `test_a_wrong_current_password_is_refused_without_writing` —— 返回 `401 invalid_old`，且版本与名称均不变（对应弹窗原地重试所依赖的信号）。

变异核验：把 `set_tenant_resource_grants` 的 `UPDATE tenants SET version=version+1` 改为 `pass` 后，`test_admin_then_grants_then_profile_commits_in_order` 失败：

```
AssertionError: 1 != 2 : the grant step bumps the version exactly once
```

### 8.2 身份域回归

```sh
.venv/bin/python -m pytest tests/test_identity_service.py tests/test_identity_service_writes.py \
  tests/test_identity_web_handlers.py tests/test_platform_admin_role_binding.py \
  tests/test_platform_user_admin.py tests/test_tenant_create_containment.py \
  tests/test_identity_concurrency_acceptance.py tests/test_identity_migration_drill.py -q
```

结果：`174 passed`。

### 8.3 真实 `identity.db` 上的既有数据一致性问题（对应任务 8.6，交运维）

只读查询真实库（未做任何写入）：

```sh
sqlite3 -readonly identity.db "SELECT t.code AS tenant, t.active AS enabled, COALESCE(cnt.n,0) AS valid_admins, CASE WHEN t.active=1 AND COALESCE(cnt.n,0)=0 THEN 'DEFECT' ELSE 'ok' END AS verdict FROM tenants t LEFT JOIN (SELECT m.tenant_id, COUNT(*) AS n FROM memberships m JOIN users u ON u.id=m.user_id JOIN membership_roles mr ON mr.membership_id=m.id JOIN roles r ON r.id=mr.role_id WHERE m.active=1 AND u.active=1 AND r.code='tenant_admin' GROUP BY m.tenant_id) cnt ON cnt.tenant_id=t.id ORDER BY verdict DESC, t.code;"
```

原始输出：

```
default|1|1|ok
test01|1|1|ok
test02|1|0|DEFECT
test03|1|0|DEFECT
test11|1|0|DEFECT
test12|1|0|DEFECT
```

**结论：4 个租户处于「已启用但没有任何有效 `tenant_admin`」状态。** 该状态与启用路径的连续性校验（`no_admin`）互相矛盾，且在§8.1 的用例 2 中被固化为可复现行为：**创建租户默认 `active=1`，而 `active=True` 的档案写入又要求存在有效管理员**，于是这类租户既「已启用」又「不可再通过写入启用」。本 change **未**自动修复该数据，也未改动创建默认值——这属于既有行为，改动它会影响创建契约，应单独评估。建议运维决策：为这 4 个租户补绑管理员，或明确「创建即启用」是否应改为「创建后需显式启用」。

一个直接相关的观察：本 change 的统一保存之所以把**管理员配置排在最前**，正是因为启用必须先有管理员，否则同一次保存中的档案步骤必定 409。§8.1 用例 1 即验证了该顺序能让「建号 + 启用」在一次保存内成功。

### 8.4 OpenSpec 校验

```sh
openspec validate tenant-editor-batch-save-and-admin-create --strict
```

输出：`Change 'tenant-editor-batch-save-and-admin-create' is valid`

### 8.5 运行环境修复（部署侧）

排查用户截图中的报错时发现运行中的服务是**旧代码**：进程 PID 34591 启动于 11:49:40，而 `auth/service.py`（12:19）、`channel/web/admin_handlers.py`（12:20）在此之后才修改。由于静态文件是从磁盘直接提供的，浏览器已经会拿到**新前端**，后端却仍是旧代码——新前端发送的 `operation: "profile"` 与 `mode: "new"` 旧后端不认，属于「新旧混跑」的危险中间态（旧后端处理 `operation=profile` 会退化为只改名、静默丢弃启用状态，正是本 change 要修的那个缺陷）。

处置：以 `launchctl kickstart -k gui/$(id -u)/com.cowagent.app` 重启（该 job 为 `KeepAlive`），新进程 PID 43458 启动于 12:54:21。核对：

- `GET /chat` → `200`；
- `GET /assets/js/identity-admin.js` 中 `withTenantPassword|tenant-password-modal|_tenantBatchSteps` 命中 9 处（新前端已生效）；
- `GET /assets/js/console.js` 中新文案密钥命中 6 处；
- 后端进程启动时间晚于后端文件修改时间（新后端已加载）。

组 5 完成后再次核对线上静态资源（前端改动无需重启进程，静态文件逐次从磁盘读取）：

```
assets/js/identity-admin.js  tenant-admin-mode-new=4  tenant-admin-body-existing=2
                             tenant-fld-admin_new_username=5  _tenantAdminBody=2
                             tenant_admin_username_taken=1  data-user-name=2
assets/css/console.css       tenant-admin-mode=6
assets/js/console.js         tenant_admin_mode_new=3  tenant_admin_username_taken=3
                             tenant_admin_new_password_required=3
```

即三个语言版本的 9 个新键均为 3 处命中，模式切换的标记与样式均已上线。

## 9. 未测试与未验收项

以下为明确**未完成**的项，不得视为已验收：

1. **真实浏览器核对（任务 4.11、8.3）未执行。** 双模式切换、密码弹窗与全页编辑器叠加时的层级、焦点、Esc 行为**只在 DOM 桩与静态 CSS 层面验证**：
   - 层级：`.tenant-password-modal` 为 `z-index: 80`，`position: fixed` 且挂在 `document.body`；编辑器为 `position: absolute; z-index: 20` 且位于 `#view-tenant` 内。二者同处根堆叠上下文（body 及其祖先未见 `transform` / `filter` / `will-change`），故 80 > 20 成立。**这属于静态推断，未在浏览器实测**。
   - Esc 与取消等价、Enter 提交、空密码不放行：已由 DOM 桩用例覆盖（且经变异核验），但未在真实键盘事件下实测。
   - 新建账号表单与模式切换的**视觉与交互**（标签换行、暗色模式、长显示名溢出、切换后的可见状态）未目视核对。
   - 未登录状态下无法进入控制台（需要平台管理员口令），因此未用浏览器代跑；建议由持有口令的操作者按 §10 步骤目视确认。
2. **双模式尚无成功的端到端记录。** 请求体字段顺序与命名已逐字段对齐 handler 契约（design §4.4）并由测试固定；后端侧 `create_tenant_admin_account` 已由 §8.2 的 pytest 覆盖。真实浏览器侧已取得**一次失败**提交的完整因果链（§11：前端确实把正确的 `mode=new` 请求打到了真实后端，后端以 `weak_password` 拒绝，前端旧逻辑隐去了原因），但**尚未观察到一次成功的浏览器端创建**，因此「成功路径 + 候选集重查」在真实环境仍属未验收。
3. **未做整体浏览器回归**：改动触及 `apiFetch` 的 401 处理、`_userPicker*` 共享助手与 `identity-admin.js` 全局作用域，虽然 `test_identity_admin_frontend.cjs`（13 项）、`test_sidebar_account_frontend.cjs`（41 项）、`test_nav_area_frontend.cjs`（5 项）均通过，但其他视图（成员、角色、组织、平台账号、审计）未做人工目视核对。
4. **候选集重查只覆盖「新建后能查到」**：分页截断（`admin_user_picker_truncated`）与搜索词筛掉新账号的情形未测；新账号排在第 101 条之后时不会出现在首屏候选中，此时操作者需用搜索框检索——该路径未测。
5. **未验证并发**：批量保存跨三个请求，仅在版本冲突（409）上有用例；两个操作者同时保存同一租户的时序未测。
6. `test_session_history_frontend.cjs` 挂起、`branding_frontend.test.cjs` 6 项失败、`test_appearance_browser.cjs` 1 项失败：均为既有问题，本 change 未修复（见 §0）。

## 10. 建议的目视确认步骤（供持有平台管理员口令者执行）

1. 重新加载 `http://localhost:9899/chat`（确保拿到新前端；必要时 Cmd+Shift+R）。
2. 进入「租户管理」→ 对任一租户点「编辑」。
3. 切到「租户管理」标签：应看到「选择已有账号 / 创建新账号」两个模式按钮，**缺省为选择模式**，新建表单不可见。
4. 在「选择已有账号」下点一个候选账号 → **显示名应被自动填成该账号的显示名**，且仍可编辑；此时切到「创建新账号」再切回来 → 之前填的显示名不应丢失。
5. 切到「创建新账号」→ 用户名、显示名、初始密码三者留空时点「保存」→ 应**不发请求、不弹密码框**，错误提示应指名当前缺的那个字段。
6. 填好三者后点「保存」→ 应弹密码框；输入正确密码 → 应成功，脏标记消失，且**再切回「选择已有账号」时候选列表里能检索到刚建的账号**（无需刷新页面）。
7. 用一个**已存在**的用户名重复第 6 步 → 应提示「该用户名已被占用…可改用选择已有账号」，且三个输入框的内容都还在。
8. 改动名称 / 启用开关，点「保存」→ 应弹出密码框；**此时不应有任何写入**；点「取消」应无写入且草稿与脏标记保留；按 `Esc` 行为应与取消一致。
9. 在密码框输入**错误**密码 → 应停在弹窗并提示「密码不正确」，而**不应**出现登录浮层或 `unauthorized`；改正后应直接成功。
10. 同时改动「基本信息」与「模型授权」再保存 → 应为一次保存串行完成（可看网络面板顺序：`/admins` → `/resources` → `/tenants/<id>`），成功后脏标记全部消失。
11. 确认密码文本在保存成功后不残留于输入框。
12. 用**少于 8 位**的初始密码新建账号 → 应提示「初始密码不满足强度要求：至少 8 位…」，且用户名、显示名、密码都还在；字段下方应始终显示该强度提示。

## 11. 验收期发现的缺陷：创建账号失败原因未透出（已修复）

### 11.1 现象与根因

验收组 5 时，操作者在真实控制台（租户 `test21`，`tnt_Fet_ez6cpY1Rf-pZ`）以「创建新账号」提交，界面只显示：

```
保存未完成，失败步骤: 租户管理
```

排查过程与证据：

1. **截图状态**：用户名 `test21`、显示名 `test21管理员`、初始密码为 **6 个字符**。
2. **排除重名冲突**：只读查询真实库，`tenants` 有 `test21`，但 `users` 中**没有** `test21`，故不是 409 用户名占用 —— 这解释了为何界面上是通用文案而非「用户名已被占用」。
3. **审计交叉印证**：`audit_events` 中最后一次写入是 `tenant.create`（test21 建租户），**没有任何失败或 `create_admin` 记录**，说明该请求在写入前就被拒（`_validate_new_account` 在 `_tx()` 之前调用，被拒路径不写审计）。
4. **确定性复现**（临时库，走真实 `IdentityService`）：

```
  screenshot (6 chars) password='123456'        -> code='weak_password' status=400 message='weak temporary password'
          also 6 chars password='Abc123'        -> code='weak_password' status=400
               7 chars password='Abc1234'       -> code='weak_password' status=400
       8 chars, common password='12345678'      -> code='weak_password' status=400
                 valid password='Str0ngTmpPass' -> OK {'membership_id': ..., 'user_id': ...}
audit actions: ['tenant.bootstrap', 'tenant.create', 'tenant.create_admin']
```

`MIN_PASSWORD_LENGTH = 8`（`auth/password.py`）。

**结论：服务端行为正确**（拒绝弱口令），**真正的缺陷在前端**：`apiFetch` 已把服务端 `code` 挂在 error 上，但 `_reportTenantBatchError` 只对 `409` 作解释，其余一律折叠为「失败步骤」，把可操作的原因丢掉了。同类问题在成员新建表单里不存在 —— 它的密码字段带 `admin_field_password_hint`（「至少 8 位…」）提示，而本次新增的初始密码字段既无提示、失败后也无说明。

未采用的取证途径（记录以免重复尝试）：
- 服务端**不记录**该失败（`admin_handlers` 直接返回错误，无日志）；`run.log` 中同时刻的 `Sessions API error: 400` 属会话列表接口，与本请求无关。
- 无法自行重放该请求：`create_tenant_admin_account` 先校验 `recent_password`（平台管理员登录密码），我无此口令。

### 11.2 修复与验证

修复分两处，均遵循既有约定而非另立规则：

1. `_tenantAdminNewReason`：按服务端 `code` 映射 `weak_password` → 「初始密码不满足强度要求…」、`invalid_username` → 「用户名不合法…」、`conflict` → 既有「用户名已被占用…」；未识别的码仍回退到「失败步骤」，不静默。
2. 初始密码字段补 `<div class="agent-field-hint">`，复用既有 `admin_field_password_hint`，与成员新建表单**同一句文案**。

TDD：新增 5 项测试（4 项先失败：弱密码说明、非法用户名说明、批量夹带基本信息时仍保留说明、字段提示；1 项为回归护栏——未映射错误码仍须报步骤，修复前后**都应通过**）。

```
tests/test_tenant_tabbed_editor_frontend.cjs   ℹ tests 47   ℹ pass 47   ℹ fail 0
tests/test_i18n_tenant_editor_keys.cjs         ℹ tests 4    ℹ pass 4    ℹ fail 0
```

变异核验（`/tmp/mut_reason.cjs`）：7/7 `CAUGHT` —— 含逐条移除三个映射、把未知码当作已知码吞掉（用以证明通用回退仍被守住）、删除提示元素、停止本地化提示、改用别的提示文案。

全量前端扫描：`199 pass / 8 fail`（修复前 `194 / 8`；+5 为本次新增用例，失败集合不变，仍为 `_tmp_repro_modeldefaults.cjs` 1、`branding_frontend.test.cjs` 6、`test_appearance_browser.cjs` 1）。

修改的 spec delta：`租户管理员配置入口收在租户管理标签` 增补「按错误码给出可操作原因、MUST NOT 仅以失败步骤代替原因」及「初始密码字段与既有入口同一强度提示」，并新增对应 Scenario。

> 影响面提示：本次修复只改善**提示**，不改变校验策略。操作者仍需提供 ≥8 位且非常见口令的初始密码；上述 `test21` 的账号**未被创建**（请求整体被拒、无残留数据）。

## 12. 验收期发现的缺陷：强制改密门禁不可见（已修复）

### 12.1 现象

操作者以新建管理员账号 `test15` 登录，点击「登录」后界面无任何变化（「点登录没有反应」）。

### 12.2 排查与根因

1. **后端正常**：`POST /auth/login` 直接调用返回 `200`，`must_change_password: true`，并返回该账号所属租户。前端代码亦健康（`login-form.onsubmit` 已绑定、`_identityMode() === 'database'`）。
2. **真实浏览器复现**（受控标签页，填入 `test15` 后点击登录）：

```json
{"err":"","errShown":"hidden","loginForm":"shown","app":"hidden","pwdValue":"",
 "accountState":{"authenticated":true,"username":"test15","mustChangePassword":true,"phase":"ready"}}
```

登录**成功**、密码被清空、应用未出现、无错误提示 —— 与操作者描述一致。

3. **定位到弹窗尺寸为零**：

```json
{"modal":{"hidden":false,"display":"flex","zIndex":"210"},
 "modal.rect":{"width":0,"height":0},
 "overlay.rect":{"width":1920,"height":1080}}
```

4. **祖先链给出元凶**：

```json
[{"id":"account-password-modal","display":"flex","rectW":0},
 {"id":"app","display":"none","hiddenClass":true,"rectW":0},
 {"tag":"BODY","display":"block"}]
```

`#account-password-modal` 是 `#app` 的子节点，而 `_enterForcedPassword()`（`console.js`）会隐藏 `#app`。被隐藏的祖先使弹窗折叠为 0×0 —— 元素自身 computed 样式仍显示 `flex/visible`，所以只看样式不会发现异常。

**根因：门禁弹窗被放在「门禁流程自己会隐藏的容器」内部。** 代码注释「Keep the login overlay as a backdrop; the password modal is elevated so it sits above the overlay」表明意图正确、结构错误。

### 12.3 归属与影响面

- **非本次改动引入**：`git diff` 显示 `_enterForcedPassword`、`_accountHidden('app'`、`#account-password-modal` 结构均不在本 change 的改动中；`chat.html` 本次改动仅为图标/文案/版本链接。
- **但直接阻断本 change 的交付**：`auth/service.py:2116-2118` 对新建账号硬编码 `must_change_password=1`，故「创建新账号」产出的每个账号首次登录即触发该门禁。真实库中 `test01-admin`、`test15` 均为该状态。
- **规范归属**：`self-password-flow` 既有需求已要求「登录和刷新 SHALL 识别 must_change_password，在租户选择/业务加载前展示同一表单」，故为**实现违反既有需求**，非新增能力。

### 12.4 修复与验证

把 `#account-password-modal` 从 `#app` 内移至 body 层（`#login-overlay` 之后、`#app` 之前）。`.agent-modal` 为 `position:fixed; inset:0`，暗色类 `.dark` 挂在 `document.documentElement`，因此移出后定位与主题均不受影响。

TDD：新增 `tests/test_forced_password_gate.cjs`（4 项，先 2 项失败）。测试直接解析真实 `chat.html` 的标签层级——既有前端测试用的 DOM 桩没有布局，无法观测零尺寸元素。

真实浏览器验证（重载后）：

```json
{"modalRect":{"w":1920,"h":1080},"cardRect":{"w":460,"h":406},
 "modalParentIsBody":true,"appDisplay":"none","firstInputFocusable":true,
 "title":"请设置新密码"}
```

修复前 `modalRect` 为 `0×0`；修复后门禁可见可交互，且 `#app` 仍为 `display:none` —— **「隐藏业务界面」与「门禁可见」第一次能同时成立**。

普通改密路径回归（`#app` 可见时打开菜单改密弹窗）：`1920×1080`，`parentIsBody: true`，正常。

```
tests/test_forced_password_gate.cjs   ℹ tests 4   ℹ pass 4   ℹ fail 0
全量前端扫描                            TOTAL pass=203 fail=8  （修复前 199/8，失败集合不变）
```

新增 spec delta `specs/self-password-flow/spec.md`：在既有需求上补入「该表单 SHALL 作为顶层浮层承载，MUST NOT 位于受限期间会被隐藏的业务容器之内」及 Scenario「强制改密门禁在业务界面隐藏时仍然可见」。`openspec validate --strict` 通过。

> 附注：验证全程未提交任何改密表单，`test15` 的密码未被修改。

## 13. 验收期新增需求：打开「租户管理」标签即只读显示当前管理员

### 13.1 需求与契约

`test15` 已有租户管理员，但打开「租户管理」标签时账号选择器为空、显示名为空，操作者无法区分「尚未指定管理员」与「已指定但未展示」。本组新增**只读**展示：打开标签即显示该租户当前有效 `tenant_admin`（多个时只显示最早绑定者）的成员显示名与登录名；选择器保持未选中、显示名输入保持为空、标签不进入未保存状态。读取失败须与「无管理员」可区分。既有「未指定管理员」空态与保存契约不变。

### 13.2 RED（先写失败测试）

```
$ .venv/bin/python -m pytest tests/test_identity_service_writes.py::TenantAdminReadTests \
    "tests/test_identity_web_handlers.py::DatabaseAuthHandlerTests::test_admins_get_returns_current_admin_readonly" -q
5 failed in 0.49s            # tenant_admins 不存在（AttributeError）
                             # GET 路由未实现：http 405 "method not allowed"

$ node --test tests/test_tenant_tabbed_editor_frontend.cjs
ℹ pass 48  ℹ fail 4
✖ the admin tab shows the current admin read-only without preselecting   TypeError: Cannot read properties of null (reading 'innerHTML')
✖ a tenant with no admin says so instead of inventing one                TypeError: ...
✖ a failed current-admin read is not reported as "no admin"              TypeError: ...
✖ a committed admin change refreshes the read-only current admin         TypeError: ...
```

### 13.3 GREEN

```
$ .venv/bin/python -m pytest tests/test_identity_service_writes.py tests/test_identity_web_handlers.py -q
116 passed in 10.92s

$ node --test tests/test_tenant_tabbed_editor_frontend.cjs tests/test_tenant_admin_account_picker.cjs \
    tests/test_tenant_create_frontend.cjs tests/test_i18n_tenant_editor_keys.cjs
ℹ tests 69   ℹ pass 69   ℹ fail 0
```

（`test_tenant_tabbed_editor_frontend.cjs` 单文件由 48 项增至 52 项；含 `test_identity_admin_frontend.cjs`、`test_forced_password_gate.cjs` 的 6 文件合并运行为 `86 pass / 0 fail`。）

实现：
- `auth/service.py` `tenant_admins(tenant_id)`：谓词与 `_count_valid_tenant_admins` 一致（成员有效 + 账号有效 + 角色为 `tenant_admin`），`ORDER BY m.created_at, m.rowid` 取最早绑定；投影仅 `membership_id / user_id / username / display_name`。
- `channel/web/admin_handlers.py` `PlatformTenantAdminsHandler.GET`：平台管理员专属，未知租户 404。
- `channel/web/static/js/identity-admin.js`：`#tenant-current-admin` 只读块 + `_renderTenantCurrentAdmin` + `_loadTenantCurrentAdmin`；`_ensureTenantAdminTab` 打开时读取；`_tenantSaveAdmin` 成功后重读；`_resetTenantAdminFields` 清除上一个租户的展示。
- `console.css` `.tenant-current-admin-card`；`console.js` 三套字典 `tenant_current_admin_label|none|unavailable`。

### 13.4 定点变异核验

| 变异 | 触发的用例 | 结果 |
| --- | --- | --- |
| `tenant_admins` 去掉 `u.active=1` | `test_excludes_disabled_account_inactive_membership_and_non_admins` | CAUGHT |
| 投影加入 `u.password_hash` | `test_lists_valid_admins_earliest_first_without_credentials` | CAUGHT |
| GET 去掉 `_require_platform_admin` | `test_admins_get_requires_platform_admin` | CAUGHT（见 13.5） |
| 读取失败仍按「无管理员」渲染 | `a failed current-admin read is not reported as "no admin"` | CAUGHT |
| 读取时预选选择器并预填显示名 | `the admin tab shows the current admin read-only without preselecting` | CAUGHT |
| 保存成功后不重读当前管理员 | `a committed admin change refreshes the read-only current admin` | CAUGHT |
| 读取时置脏标签 | `showing the current admin does not make the tab unsaved` | CAUGHT |

### 13.5 修正一处弱断言（变异核验发现）

`test_admins_get_requires_platform_admin` 最初用 `create_member` 新建的账号登录取证；该账号带 `must_change_password=1`，而受限会话在 `_require_context` 即以 403 `password_change_required` 被拒——**即使删掉平台管理员校验，断言仍会通过**。已改为先把 `must_change_password` 清零再登录，并同时断言 `code == "forbidden"`；重跑变异（删除 `_require_platform_admin`）确认 `CAUGHT`。

### 13.6 未测试 / 已知边界

- **真实浏览器目视未执行**：与 tasks 8.3 相同，当前会话无平台管理员口令，未登录控制台核对只读块的实际视觉与布局。DOM 桩 + 变异核验已覆盖行为契约，静态样式以既有 `.tenant-space-card` 皮肤推断。
- **全量 `node --test tests/*.cjs` 未跑完**：`tests/_tmp_repro_modeldefaults.cjs`（一次性复现脚本，本不属套件）另有失败；`tests/test_session_history_frontend.cjs` 在运行中使测试进程挂起，且其 17 项失败集中在搜索/输入法用例。经 `git diff` 核对，本次对 `console.js` 的改动仅落在 I18N 字典块，历史会话用例所切片的历史处理区段不在工作树 diff 内，故这些失败与挂起**非本次改动引入**，未在本 change 内处理。
- 多个有效管理员时只展示最早绑定者：`test_lists_valid_admins_earliest_first_without_credentials` 覆盖排序；「其余管理员不展示」由前端用例 `...only the earliest admin is shown...` 覆盖。

### 13.7 spec 更新

`openspec/changes/.../specs/tenant-management/spec.md`：在 `平台直接配置管理员且不接管账号` 补入只读查询能力与 Scenario「读取租户当前管理员」；在 `租户管理员配置入口收在租户管理标签` 补入默认只读展示、无管理员与读取失败三条契约及对应 Scenario。

## 14. 验收期缺陷：GET 路由被 HTTP 方法策略门禁拒绝（405）

### 14.1 现象

上线核对时，「租户管理」标签显示橙色提示「当前管理员信息读取失败，可重新打开该标签重试」。前端行为正确（拒绝把失败伪装成「无管理员」），但读取确实失败。

### 14.2 排障与两个独立原因

**第一步（必要的，但不充分）**：后端进程 `43458` 启动于 `12:54:21`，早于本次 Python 改动（`admin_handlers.py` 14:18、`service.py` 14:22），且 `app.py` 未开启 autoreload。判别探测：

```
GET  /api/platform/tenants/test15/admins  → 405 {"code":"method_not_allowed"}
POST /api/platform/tenants/test15/admins  → 401 {"code":"unauthorized"}
```

重启后取到新进程（`60987`，`14:24:58`），**GET 仍为 405**。说明重启并非充分原因。

**第二步（真正的主因）**：405 的响应体是 JSON 且带 `code=method_not_allowed`；而 web.py 原生 `NoMethod` 返回 `text/html` 与 `message = "method not allowed"`，不带 `code`。据此定位到应用自身的门禁：

```
auth/http_policy.py:303   return _json_error("method not allowed", 405, "method_not_allowed")
auth/http_policy.py:90    "/api/platform/tenants/([^/]+)/admins": {"POST": ...}   # 只有 POST
```

`enforce_http_policy` 是装在 `build_web_app()` 上的处理器，按「路径 + 方法」做**注册完备性**校验：路径命中但方法未登记时，在到达 handler 之前即返回 405。该路由只登记了 `POST`，故 `GET` 永远到不了 `PlatformTenantAdminsHandler.GET`。

> 该表本身有注释说明其设计意图（`http_policy.py:80-84`：未登记的方法「意味着完备性门禁返回 405 而不是调用没有该方法的 handler」）。本缺陷正是「handler 新增方法但策略表未同步」这一类，仓库内已有同型先例与其护栏 `test_session_settings_get_registered`。

### 14.3 为什么后端测试全绿而真实浏览器失败

新增的 `tests/test_identity_web_handlers.py` 用例用 `self._app()` 构造的是**裸 `web.application`**（自有路由表），未安装 `enforce_http_policy`，因此只覆盖了 handler，覆盖不到真实门禁。真实的门禁由 `web_channel.build_web_app()` 安装。这是本次测试盲区，已由 §14.4 的两个用例补上。

### 14.4 修复与护栏（TDD）

先写失败测试（`tests/test_http_policy.py`）：

1. `test_tenant_admins_read_registered`：对 `GET` 与 `POST` 均断言 `_match_policy` 命中且 `policy == "platform"`。
2. `test_tenant_admins_get_reaches_handler_through_real_app`：经 `build_web_app()` 发 GET，断言**不是 405**、而是 handler 自身的 401。这条正是能复现浏览器现象、并在修复前失败的断言。

RED：

```
$ .venv/bin/python -m pytest tests/test_http_policy.py -q -k tenant_admins
FAILED test_tenant_admins_get_reaches_handler_through_real_app
FAILED test_tenant_admins_read_registered
2 failed, 16 deselected
E   AssertionError: unexpectedly None : GET is not registered for the admins route
```

GREEN：在 `auth/http_policy.py` 的该路由补 `"GET": {"policy": "platform", ...}`。

```
$ .venv/bin/python -m pytest tests/test_http_policy.py -q
18 passed

$ .venv/bin/python -m pytest tests/test_http_policy.py tests/test_identity_web_handlers.py \
    tests/test_identity_service_writes.py -q
134 passed
```

重启服务后复核（`61811`，`14:27:18`）：

```
GET  /api/platform/tenants/test15/admins  → 401 {"code":"unauthorized"}   # 已到达 handler
DELETE /api/platform/tenants/test15/admins → 405 {"code":"method_not_allowed"}  # 门禁未被放宽
```

GET 由 405 变为 401，证明方法已登记且请求进入 handler 的鉴权路径；`DELETE` 仍为 405，证明完备性门禁整体未被削弱（本次只登记了一个确有实现的方法）。

### 14.5 真实库预期值（只读核对）

```
tenant: tnt_EA3qM-lHPLD8ZPwW / test15 / active=1 / version=3
valid tenant_admins: [{'username': 'test15', 'display_name': 'test15管理员'}]
```

故重载页面后该标签应显示「当前租户管理员：test15管理员 / test15」。

### 14.6 未纳入本次的范围

一个更通用的护栏（遍历 `app.mapping`，断言每个 handler 已实现的方法都在 `ROUTE_POLICY` 中登记）经试算会命中 **11 处既有、且部分为有意为之**的差异（如 `/api/platform/users/([^/]+)` 的 GET 被刻意留空以返回 405、`/api/sessions/(.*)` 的 DELETE/PUT 等），无法在本次范围内收敛。因此沿用仓库既有的定向护栏写法，未引入该通用断言。

### 14.7 更正记录

前一轮回复曾把原因归结为「仅需重启服务」。该结论**不完整**：重启只解决了进程陈旧（`12:54` 的旧代码），GET 在重启后仍为 405，主因是 `http_policy.py` 未登记 GET。两处原因均已修复，并已删除该局限结论的表述。
