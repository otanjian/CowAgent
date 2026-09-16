# 8.4c i18n 黄金快照的漂移规模与定向对账

对应 `tasks.md` 8.4（i18n 回归）。本文件纠正 `evidence/8-2-compat-cycle.md` §7 与
`evidence/8-4-frontend-baseline.md` 末节对 `tests/fixtures/console_i18n_snapshot.json`
漂移的低估，并给出逐键归属、三语齐备性检查与「测试仍非空洞」的验证。

## 1. 结论

`tests/test_console_i18n_parity.cjs` 的 2 条红**不是**「另一路删了 5 个 `account_menu_*`
键」造成的 2 个键位差。实测漂移为 **74 个键级 delta × 3 语 = 222 个翻译单元**，跨本 change
的 10 个任务（3.2 / 3.3 / 3.4 / 3.5 / 4.4 / 4.5 / 4.6 / 5.1 / 5.1b / 5.4 / 6.1）与 1 个已归档
change（`upgrade-personal-channel-workbench` 阶段 3），是多个切片陆续加入注册表、快照从未
真正跟上的累积结果：

```
键级 delta 74 = 新增 68 + 删除 5 + 改值 1        （每语各自成立）
每语 Δ = +68 / −5 / ~1
修复后 zh:1359  zh-Hant:1352  en:1359            （与活注册表逐语相等）
```

## 2. 命令与前后结果

```
node --test tests/test_console_i18n_parity.cjs
修复前 → tests 5 / pass 3 / fail 2
         ✖ the merged namespace table deep-equals the pre-split snapshot
           AssertionError: the split must not add, drop, rename, or alter any translation
         ✖ each namespace declares the same keys in every language it uses
           AssertionError: admin-console/zh must carry exactly the snapshot keys for that language
修复后 → tests 5 / pass 5 / fail 0
```

全量前端：

```
node --test tests/*.cjs
修复前（本文件核对前的一次取样）→ tests 699 / pass 654 / fail 45
修复后                          → tests 699 / pass 656 / fail 43
```

修复后的 43 条逐项等于既有基线，`test_console_i18n_parity` 不再出现在失败集：

| 文件 | 条数 | 归因（既有，非本轮） |
| --- | --- | --- |
| `tests/test_session_history_frontend.cjs` | 36 | 沙箱缺 `queueMicrotask` |
| `tests/test_sidebar_account_frontend.cjs` | 5 | 既有产品/测试口径分歧（见 `8-4-frontend-baseline.md` 更正一） |
| `tests/test_appearance_browser.cjs` | 1 | 浏览器 harness（见 8.7 的重定性） |
| `tests/_tmp_repro_modeldefaults.cjs` | 1 | 已提交的 TEMP repro，DOM 桩缺 `document.querySelector` |

## 3. 为什么不能整表重生成

`tests/fixtures/console_i18n_snapshot.json` 是**拆分前快照**，该用例的全部价值在于它会把
「新增 / 删除 / 改名 / 改值」判为失败。若用活注册表重新生成快照，断言立刻退化成
「注册表等于注册表」，从此对任何后续漂移恒真——这正是本 change 反复出现的失败模式
（只核对内部自洽、不核对是否为真）。因此本次对 74 个 delta **逐个建立归属**后才改快照，
并在改完后用变异验证证明该用例仍有牙齿（§6）。

同步时只做键级插入 / 删除 / 改值，不重排、不格式化整表：改动为
`+207 / −18`（相对本次核对前的工作区文件；含改值行的 −3/+3），
`git diff --numstat` 对 `HEAD` 显示 `261 / 21`，其中 `54 / 3` 是另一路在途改动
新增的 18 个 `resource_detail_*` / `models_catalog_*` 键（见 §7）。

## 4. 逐键归属表

归属判据 = 任务台账 + evidence 文件 + 代码使用点三者一致。**没有无法归属的键。**

| 键（族） | 键数 | 归属 | 证据 / 代码落点 |
| --- | --- | --- | --- |
| `admin_home_kpi_retry`, `admin_home_kpi_unavailable` | 2 | 3.5 概览按范围下发 | `evidence/3-5-overview-by-scope.md` §4（区域级「部分数据暂时无法读取 + 重试」）；`console.js:1676` |
| `agent_disabled` | 1 | 3.2 连带回归（停用对象不可对话） | `evidence/3-2-legacy-personal-acceptance-gap.md` 已修表第 1 行；`console.js:2574`（`agentUnavailableLabel`） |
| `agents_set_my_default`, `_conflict`, `_disabled`, `_done`, `_failed`, `_forbidden` | 6 | 4.4 用户默认智能体 | `evidence/4-4-user-default-agent.md` §4「文案（三语）」明列这 5 个后缀键；`console.js` `setAgentAsMyDefault` |
| `agents_set_default_private` | 1 | 4.5 租户默认拒绝私有目标 | `evidence/4-4-user-default-agent.md` §7 第 3 行；`console.js:3770` |
| `agents_set_tenant_default` | 1 | 4.4/4.5「设为默认」拆分为租户动作 | `evidence/4-4-user-default-agent.md` §4；`console.js:2994` |
| `agents_anchor_source_own`, `_shared`, `_tenant`, `_unknown`, `_user` | 5 | 4.6 默认解析的回落来源 | `evidence/4-6-default-initialisation-and-source.md`（三语各 5 键）；`console.js:2579-2591` `agentAnchorHintText()` |
| `memory_clear`, `_msg`, `_ok`, `_title`, `memory_cleared`, `memory_delete`, `_msg`, `_ok`, `_title`, `memory_deleted`, `memory_index_pending` | 11 | 5.1b 记忆写入路径（增删清空 + 索引待重试） | `evidence/5-1b-write-path-and-acceptance.md`「三语文案」行；`console.js:12643`、`:12711` |
| `memory_target_personal` | 1 | 5.1 记忆目标集合 | `evidence/5-1-memory-target-set.md:61`；`console.js:4511` |
| `skill_global_toggle_managed` | 1 | 5.4 公共面写入资格（不广告必败动作） | `evidence/5-4-public-surface-authority.md` §1 第三处；`console.js:11930`、`:12094` |
| `personal_channels_*`（binding / status / target / repair / credential / quota / policy 等） | 37 | 已归档 change `upgrade-personal-channel-workbench` 阶段 3 | `openspec/changes/archive/2026-09-15-upgrade-personal-channel-workbench/evidence/3-frontend-workbench-evidence.md` §3.6（明列「三语 116 键」中新增 37 个 `personal_channels_*`） |
| `tenant_channel_self_desc` | 1 | 6.1 共用渠道面按范围出文案 | `evidence/6-1-shared-channel-surface.md:165` |
| `tenant_channel_empty_desc_self` | 1 | 6.x 渠道空态按范围出文案（3.6 现场发现） | `evidence/3-6-browser-dual-role-acceptance.md` §5 |
| **删除** `account_menu_resources`, `_checking`, `_failed`, `_retry`, `account_menu_region_personal` | 5 | 3.3/3.4 账号菜单收成「账号设置」单面 | `evidence/3-3-account-menu-resources-removed.md` 变更表（`i18n/account.js` 行） |
| **改值** `account_menu_trigger_hint` | 1 | 3.4 触发器提示口径 | 同上；三语 `個人資源與設定` / `Personal resources and settings` → `账号设置` / `帳號設定` / `Account settings` |

一处 evidence 文本笔误（不影响归属）：`4-6` 证据写作 `agents_anchor_source_any`，实际键名为
`agents_anchor_source_own`（取值「回落到你本人的私有智能体」与 `own` 相符）。

## 5. 三语齐备性

对 68 个新增键逐一检查 zh / zh-Hant / en：

```
新增键总数 68，未在三语齐备的键数 = 0
```

即新增键本身**没有**翻译缺口。另有一组**既有**缺口不属本次范围，也不借同步补齐：
zh-Hant 相对 zh/en 少 7 键，与 `HEAD` 上的缺口逐项相同（该用例的注释即声明
「zh-Hant is missing a handful of keys that zh/en have」，并按语言各自对齐快照）：

| 命名空间 | zh-Hant 缺少的键 |
| --- | --- |
| `agents` | `agents_tab_tasks`, `agents_tasks_label` |
| `core` | `optimize_idle_title`, `optimize_busy_title`, `optimize_error`, `optimize_empty` |
| `tasks-records` | `tasks_empty_agent` |

这些是**真实的翻译缺口**，在此登记而不修改：补齐需要在没有任何产品文案依据的情况下新造
繁体文案，且超出 8.4 的回归范围；本 change 也不应替既有缺口背书（该用例正是按语言各自对齐
快照，未把它们判为失败）。

## 6. 牙齿验证（证明用例不是空洞的）

在 `/tmp` 的**隔离副本**（`tests/test_console_i18n_parity.cjs` + 快照 + `console.js` +
`i18n/*.js`）中注入三类漂移，确认用例转红；副本删除后工作区无残留：

| 变异 | 期望 | 实测 |
| --- | --- | --- |
| 副本基线（未变异） | 5/5 通过 | tests 5 / pass 5 / fail 0 |
| M1 三语改名 `memory_clear_title` → `memory_clear_title_renamed` | 深度相等 + 逐语齐备转红 | tests 5 / pass 3 / fail 2（`must not add, drop, rename, or alter any translation`；`core/zh must carry exactly the snapshot keys`） |
| M2 只改 en 一个取值（`agents_set_my_default_done`） | 深度相等转红 | tests 5 / pass 4 / fail 1 |
| M3 只从 **zh-Hant** 删 `memory_delete_title` | 深度相等 + 逐语齐备转红 | tests 5 / pass 3 / fail 2 |
| 副本复原 | 5/5 通过 | tests 5 / pass 5 / fail 0 |

即：新增 / 删除 / 改名 / 改值**以及单语缺键**都会被捕获，用例在本次同步后未退化为恒真。

## 7. 核对时发现的三处「声称已同步」不成立（只登记，不改他人证据）

`evidence/3-3-account-menu-resources-removed.md` 末行、`evidence/3-6-browser-dual-role-acceptance.md`
§4、以及归档 `upgrade-personal-channel-workbench` 的 `3-frontend-workbench-evidence.md` §3.6
都声称已把对应键写入快照并给出「5/5 通过」；但本次核对时该快照**并不含**这些键。核对时刻的
工作区快照相对 `HEAD` 只有一处改动：另一路在途工作新增的 18 个
`resource_detail_*` / `models_catalog_*` 键（`git diff --numstat` = `54 / 3`），既没有
3.3 的 5 删 1 改，也没有 3.6 / 归档声称的 55～68 键新增。故上述三处的「已同步 / 5/5 通过」
在本工作区**不可复现**；本文件的 74 键归属与同步是首次落到文件上的对账。

未追查该落差的原因（可能是一路写回丢失或被并行写入覆盖；快照是未提交工作区文件，
没有可用的历史）。在此只记录事实，供 8.6 收口时判断是否还需要一遍跨证据的快照复核。

## 8. 未覆盖 / 未验证

- 本次只动快照，未改任何产品代码；`i18n/*.js` 未改（§5 的 7 个既有缺口按上文保留）。
- 未在真实浏览器里核对 74 个文案键的**显示效果**（只核对了键集与取值逐语相等）；
  文案在页面上的排版/截断属 3.6 / 8.7 的浏览器范围。
- 「快照相对 `HEAD` 的另 18 键（`resource_detail_*` / `models_catalog_*`）」由另一路在途工作
  负责，本文件只确认它们三语齐备且与活注册表相等，不替其功能验收。
