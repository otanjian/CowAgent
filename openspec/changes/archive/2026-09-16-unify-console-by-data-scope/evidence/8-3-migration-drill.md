# 8.3 菜单与偏好迁移演练：幂等、中断补偿、显式关闭、分片启用与恢复

对应 `tasks.md` 8.3：「完成菜单及偏好迁移幂等、中断补偿、显式关闭保持、只读/运行分片
启用与恢复演练；回退不恢复私有读取或默认转共享旁路。」

演练的对象是**发布过程**，不是迁移函数的语义。区别很关键：`_migration_25` /
`_migration_26` 的行为已经由既有用例固定（§1），但「在真实的迁移执行器上，一次升级
被打断在链条中间会发生什么、重试之后是否与一次跑完等价、回滚数据之后旧行为会不会
复活」——这三件事原来没有覆盖（§2）。本任务新增
`tests/test_console_migration_drill.py`（12 项）补齐，**未修改任何既有测试文件**。

## 1. 既有覆盖（先读，避免重复）

| 主题 | 既有覆盖 | 覆盖到哪一步 |
| --- | --- | --- |
| 偏好迁移 `_migration_25`（合法默认保留、来源回填、四类非法指针清理、逐条审计、重开不重跑、版本只记一次） | `tests/test_user_default_migration.py`（21 项） | 直接**调用函数**，不经过执行器 |
| 租户默认的运行修复 | `tests/test_management_repair_tenant_defaults.py` | CLI 干跑/应用/幂等 |
| 菜单映射 `_migration_26`（映射、多对一、非菜单不动、自定义角色、不复活、重开不恢复） | `tests/test_console_menu_mapping.py`（29 项） | 直接**调用函数**，不经过执行器；撤权用真实 `IdentityService.update_role` |
| 个人实例目标修复 | `tests/test_personal_instance_target_repair.py` | 无效目标可见可收口 |
| 交付演练 D1–D8（重开不升级、**链尾追加一条假迁移**后回滚重试、写入中断、开关撤回、一致性备份恢复、退役管理员私有读取被拒） | `tests/test_personal_delivery_drill.py`（8 项，D1–D8） | 见 §4 的边界说明 |
| 开关只控制「是否开放」、不替代授权；撤回不等于破坏已验收功能 | `tests/test_personal_capability_switches.py` | 见 §4 |

既有 D2 的中断是**在链条末尾追加一条自造迁移**再让它抛错，证明的是执行器的通用
回滚语义。它没有覆盖真实链条内部的断点：25 已经提交而 26 失败时，26 是否整体回滚；
25 自己失败时，它新加的列是否随事务一起回滚；重试之后是否与一次跑完逐字段相同。

## 2. 新增覆盖：`tests/test_console_migration_drill.py`（12 项）

夹具形态：把 1..24 版在临时库里重放，写入「升级前机器上真实存在的库」——既有旧菜单
授权（内置 `member`/`tenant_admin` + 一个带旧 id 的自定义角色 + 一个完全没有旧 id 的
自定义角色），也有四类默认指针（合法私有、合法共享、指向他人私有、明确没有偏好），
租户默认指向一个私有 Agent。然后**只打开真实的 `IdentityStore`**，让真正的执行器跑
25、26——以及链尾后来新增的 27（观测时链长为 27，见 §2.1 的「加法健壮」说明）。

### 2.0 断言为什么写成「加法健壮」

本演练的夹具是**旧库**，而链条会继续增长：观测期间另一路工作就在链尾加了
`_migration_27`（为内置角色追加 `nav:admin.models`）。因此新用例断言的是 26 自己的
不变量——「旧 id 全部消失、映射目标在位且不重复、版本恰好 +1、非菜单授权不动、每个被
映射角色恰好一条审计」——以及「被删除的行 ⊆ 旧个人菜单授权行」；**不**断言某个角色的
菜单授权集合恰好等于某个列表。否则每加一条只做追加的迁移，本演练都会变成假失败。

### 2.1 幂等（重复执行）

| 用例 | 断言 |
| --- | --- |
| `test_the_chain_maps_every_role_that_held_a_legacy_id` | 五个旧 id 全部消失、映射到的三个正式页各恰好一条（工具/技能多对一不得重复）、`r_member`/`r_admin` 版本恰好 +1、非菜单授权原样、`r_quiet` 版本不变、全库 `nav:personal.%` 计数为 0、每个被映射角色恰好一条映射审计（共 3 条） |
| `test_reopening_after_the_chain_neither_re_maps_nor_re_audits` | 再打开两次，授权/版本/审计/指针/归属**全量指纹**不变 |
| `test_a_member_with_no_preference_is_never_backfilled` | 「明确没有偏好」在重复升级后仍是 `NULL`/`NULL` |

### 2.2 中断补偿（真实链条中间的断点）

断点靠 `_new_migration_id` 的第 N 次调用注入——25 在这个夹具里恰好分配 2 次（1 个租户
修复 + 1 个成员修复），所以第 3 次必然落在 26 的第一个角色上；第 1 次落在 25 内部。

| 用例 | 断言 |
| --- | --- |
| `test_a_crash_inside_the_menu_step_rolls_the_whole_step_back` | 25 已提交（版本表 `1..24,25`），**26 整体回滚**：旧 id 原样、版本不变、`role.menu.legacy_personal_mapped` 审计 0 条；重试后与另建的干净库**逐字段相同**，且映射审计恰好 3 条（不是 6 条） |
| `test_a_crash_inside_the_preference_step_rolls_back_its_columns_too` | 25 未提交：`default_agent_revision` / `default_agent_origin` 两列**不存在**（`ALTER TABLE` 随事务回滚）、租户默认与 `m_erin` 指针未变、审计 0 条；重试后两列就位、修复恰好各 1 条，且**链条走到 26**（映射 3 条），不会停在 25 |
| `test_the_retry_after_a_crash_equals_a_clean_upgrade` | 同一个断点，「断+重试」与「一次跑完」的六张表指纹完全相等（这是「重复与重试不产生重复副作用」的最强表达） |

### 2.3 显式关闭保持

| 用例 | 断言 |
| --- | --- |
| `test_a_member_with_no_preference_is_never_backfilled` | 没有偏好不被租户默认回填 |
| `test_a_withdrawn_menu_grant_is_not_restored_by_a_restart` | 管理员删掉 `nav:admin.memory` 后重开两次：授权集合与版本不变，且旧 id `nav:personal.memory` **不复活** |

### 2.4 回退不恢复旧行为

| 用例 | 断言 |
| --- | --- |
| `test_rolling_the_data_back_cannot_reopen_the_retired_state` | 把升级前的库**盖回** `identity.db`（发布回滚最常见的形态）再用当前代码启动：旧个人授权被重新收口为 0 条、租户默认仍为 `NULL` 且**没有被替成共享 Agent**（不是「默认转共享旁路」）、指向他人私有的 `m_erin` 仍为 `NULL`、归属不变、每条修复恰好一条审计 |
| `test_a_repair_keeps_the_private_owner_of_every_agent` | 升级前后全部 `(agent_id, private_owner_user_id)` 对不变 |
| `test_no_migration_in_the_chain_assigns_a_private_owner` | 静态护栏：链条里没有任何一版给 `private_owner_user_id` 赋值、也没有任何一版重建 `agent_bindings`（重建的列拷贝清单一旦漏列，归属会被静默清空，不会有赋值语句可查） |
| `test_the_upgrade_deletes_no_business_row` | `agent_bindings`/`users`/`tenants`/`memberships`/`roles` 行数不变、非菜单授权行数不变；**被删除的行集合 ⊆ 旧个人菜单授权行**（链条允许追加，如 27 的 `nav:admin.models`，但删除只允许落在退役的菜单授权上） |

私有读取这一侧的「回退不恢复」另有既有覆盖：D8（`test_d8_the_retired_administrator_private_read_is_still_forbidden`）
证明退役的管理员私有读取旁路仍被拒，且判据与角色无关。

### 2.5 分片启用与恢复

| 用例 | 断言 |
| --- | --- |
| `test_the_chain_does_not_enable_a_capability_switch_or_a_runtime_shard` | `personal_channel_runtime` 出厂值为 `False`；升级后 `tenant_channel_instances` 与 `credentials` 仍是 0 行——**迁移不启用分片、不建运行实例** |

真正的「运行分片启用与恢复」不在这里，见 §4。

## 3. 变异验证（演练必须会在回归时失败）

只在临时副本（`/tmp`，未改工作区）里做过两次变异，撤销后回到全绿：

| 变异 | 预期失败 |
| --- | --- |
| `_migration_25` 在两条 `ALTER TABLE` 之后加一次 `con.commit()`（模拟「非原子的偏好步」） | `test_a_crash_inside_the_preference_step_rolls_back_its_columns_too`、`test_the_retry_after_a_crash_equals_a_clean_upgrade` 失败（重试撞 `duplicate column name`，正是半迁移状态的不可重试形状） |
| `_migration_26` 不再删除旧 id（把 `DELETE` 换成自赋值 `UPDATE`） | `test_the_chain_maps_every_role_that_held_a_legacy_id`、`test_a_withdrawn_menu_grant_is_not_restored_by_a_restart`、`test_rolling_the_data_back_cannot_reopen_the_retired_state`、`test_the_upgrade_deletes_no_business_row` 失败 |

两次变异在 §2.0 的「加法健壮」改写之后**重新跑过一遍**，失败集合不变（第一次仍是 2 项，
第二次 4 项），说明改写没有把断言磨钝。

## 4. 边界与未覆盖

* **运行分片的真实启用**：`test_personal_delivery_drill.py:289-311`（D6）在**运行缝**上
  验证了「开关打开 + 某类型已验收 → 连接被合成；开关撤回 → 连接停止且报「已保存未连接」
  而不是失败」。但那里的「已验收类型」是把 `PERSONAL_RUNTIME_ACCEPTED_TYPES` 打补丁成
  `{'feishu'}` 得到的**演练杆**，不是真实提供方往返。因此「运行分片在真实资源条件下启用
  与恢复」记为**未覆盖**，原因是阶段 7 阻塞：需要同一把 `COW_CREDENTIAL_MASTER_KEY`、
  一个真正装配 `_channel_mgr` 的运行进程、以及 feishu/dingtalk/wecom_bot 中任一提供方的
  真实凭据（见 `evidence/7-1-runtime-preflight.md`）。**不得**据此把 7.4/7.6 记为通过。
* **只读分片**：内存/目录类只读切片出厂即开，其「启用」是既有行为，
  `tests/test_personal_capability_switches.py` 与 `evidence/5-4-public-surface-authority.md`
  覆盖；本任务只新增「迁移不替部署打开分片」这一半。
* **代码回退（二进制回滚）**：无法在测试里表达（要跑旧版本二进制）。本任务覆盖的是
  **数据回退**（把升级前的库盖回去）这一半，也正是运维最常用、最容易悄悄恢复旧行为的
  那一半。
* **一致性备份恢复**：D7 已覆盖（恢复后不需要再迁移、数据存活、恢复不启动个人实例、
  跨租户与管理员拒绝在恢复后仍成立），本任务不重复。

## 5. 证据

```
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_console_migration_drill.py \
  -q -p no:randomly
→ 12 passed (0:00:01)

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_user_default_migration.py \
  tests/test_personal_instance_target_repair.py tests/test_management_repair_tenant_defaults.py \
  tests/test_console_menu_mapping.py tests/test_console_migration_drill.py -q -p no:randomly
→ 99 passed, 23 subtests passed (0:01:12)

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_personal_delivery_drill.py \
  tests/test_personal_capability_switches.py -q -p no:randomly
→ 36 passed (0:00:35)
```

以上三条在 **2026-09-16 02:50** 重跑一遍（结论未变），时间点是并行 worker 正在关闭 5.4/5.5 的窗口内：
本次演练引用的 `auth/store.py`（sha256 `5db362050c3ee4ca`）、`auth/policy.py`（`7328981c17450eb6`）
在该窗口内未变动，故 §2.2 的断点位置（`_new_migration_id` 调用次序）与链条长度（27）仍然成立。

| 演练项 | 结论 |
| --- | --- |
| 幂等（重复执行） | ✅ 已验收（§2.1） |
| 中断补偿（真实链条中点） | ✅ 已验收（§2.2），含「与一次跑完等价」 |
| 显式关闭保持（偏好 / 菜单） | ✅ 已验收（§2.3） |
| 回退不恢复私有读取与默认转共享旁路 | ✅ 已验收（§2.4，数据回退侧；私有读取侧见 D8） |
| 迁移不启用分片 | ✅ 已验收（§2.5） |
| 只读分片启用 | ✅ 既有覆盖（开关语义） |
| 运行分片真实启用与恢复 | ❌ **未覆盖**：阻塞于真实提供方凭据与运行进程（§4；7.4/7.6） |
| 旧代码二进制回退 | ❌ **未覆盖**：不可在测试中表达（§4） |
