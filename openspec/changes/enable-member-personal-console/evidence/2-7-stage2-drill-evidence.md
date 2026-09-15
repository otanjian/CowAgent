# 2.7 阶段二证据：真实身份库上的迁移、中断恢复与配额并发

- 任务：`2.7 完成真实临时身份库上的迁移、重复迁移、中断恢复、唯一约束和双请求配额竞争测试，形成进入下一阶段的证据。`
- 日期：2026-09-14
- 被测对象：`_migration_16` ~ `_migration_20`、阶段二新增表与列、`IdentityService.consume_quota` 的双请求竞争
- 测试文件：`tests/test_stage2_migration_and_quota_evidence.py`（17 项，全绿）

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| 迁移在真实临时库上落地 | 通过：一次会话内重复打开同一文件多次，版本集合、菜单 grant、阶段二数据均不变 |
| 中断恢复 | 通过：失败的迁移**不记录版本**且**不留部分行**；重试后完整落地且无重复 |
| 唯一约束 | 通过：个人配置 `(tenant,user,kind,resource)`、个人渠道关联索引由数据库层强制 |
| 双请求配额竞争 | **未超发**：硬限 1 时两并发恰好一胜一败，败者由**限额判定**拒绝（有 `quota.deny` 审计），用量恰为 1 |
| 关闭门槛 **Q1** | **已解除**（附方法学限制，见 §5） |

## 1. 为什么这些必须在真实库上跑

迁移正确性与配额原子性无法靠阅读代码断言：前者取决于 SQLite 的 DDL 行为与事务边界，后者取决于 `BEGIN IMMEDIATE` 的加锁时机。因此本组测试**不 mock store**：全部在磁盘上的临时 `identity.db` 上运行。

## 2. 迁移与重复迁移（`RealDatabaseMigrationTests`）

- `test_a_fresh_database_records_every_migration_exactly_once`
  新库记录 `schema_migrations = 1..N`，无重复、无缺号。
- `test_the_stage_two_tables_and_columns_exist`
  阶段二结构确实落地：`binding_challenges`、`personal_channel_links`、`personal_resource_configs` 三张表；
  `tenant_channel_instances.scope/owner_user_id`、`agent_bindings.origin`、`credentials.owner_user_id` 三处新列。
- `test_reopening_the_same_file_repeatedly_changes_nothing`
  同一进程内再打开 3 次：版本集合不变，`role_resource_grants`（menu）数量不变
  ——证明 `_migration_20` 的补入不会在每次启动时重复累加。
- `test_repeat_migration_preserves_stage_two_rows`
  先写入个人配置，再打开 3 次：`personal_resource_configs` 与 `credentials` 行数不变。

## 3. 中断恢复（`InterruptedMigrationRecoveryTests`）

做法：把最新的 `_migrations[19]`（即 `_migration_20`）替换成一个**先真实写入再抛异常**的函数
（插入一个 `tenants` 行、一个 `roles` 行、三条 `role_resource_grants`，然后 `raise`），
由真实的迁移运行器 `IdentityStore._migrate()` 驱动。

- `test_an_interrupted_migration_is_not_recorded`
  失败后版本 20 **未被记录**，已记录集合 = `1..19`。
- `test_an_interrupted_migration_leaves_no_partial_rows`
  失败后 `tenants` / `roles` / 新增 grant 计数均为 **0** —— `ConnGuard.__exit__` 的 rollback 生效，
  不是「因为什么都没写所以看起来干净」。
- `test_retrying_after_an_interruption_completes_and_does_not_duplicate`
  重试后版本推进到 20 且等于完整集合；中断尝试的探针行数为 0；
  全库 `GROUP BY (role_id, kind, resource_id) HAVING COUNT(*)>1` 为空 —— 无重复。

## 4. 唯一约束（`StageTwoUniqueConstraintTests`）

- 个人配置：同一 `(tenant, user, kind, resource)` 二次直插触发 `sqlite3.IntegrityError`。
- 个人渠道关联索引存在（`idx_personal_channel_links_identity`）。
- 凭据租户内名称唯一索引存在（`idx_credentials_tenant_name`）——与新增的 owner 列并存，
  即「owner 作用域」没有破坏原有的租户内名称唯一性。

## 5. 双请求配额竞争（`QuotaDoubleRequestRaceTests` / `WriteLockSerializationTests`）

### 5.1 结果类测试（真实双线程 + `threading.Barrier` + 真实文件库）

| 场景 | 断言 |
|---|---|
| 租户硬限 1，两并发各 1 | 结果恰为 `[False, True]`；用量恰为 1 |
| 租户硬限 2，两并发各 1 | 结果恰为 `[True, True]`；用量恰为 2 |
| 用户桶硬限 1 | 结果恰为 `[False, True]` |
| 硬限 3，两并发各 2 | 结果恰为 `[False, True]`；用量恰为 2（不部分计数） |

关键补充断言：硬限 1 场景下 `audit_events` 中恰有 **1** 条 `action='quota.deny'`。
这一条把「败者被**限额判定**拒绝」与「败者因拿不到写锁而 fail-closed」区分开——
两者都能保证不超发，但只有前者是本任务要证明的性质。

结果类测试连跑 5 次均稳定通过（无抖动）。

### 5.2 方法学限制（重要）

对结果类测试做变异验证时发现：

- 去掉租户限额判定（`if limit_row and hard_limit > 0:` → 恒假）：**被发现**，测试失败。
- 把 `_tx()` 的 `BEGIN IMMEDIATE` 换成延迟 `BEGIN`：**未被发现**，测试仍然通过。

原因：两线程并未被强制交错。延迟 `BEGIN` 下若两者真的重叠，第二次写会撞锁并 fail-closed，
结果仍是「一胜一败」，所以**结果类测试无法区分机制差异**。
这正是 Q1 当初「证据不足」的实质——只跑通过、不辨机制。

因此补做 `WriteLockSerializationTests`，把竞争**确定性地**构造出来：

- `test_an_immediate_transaction_takes_the_lock_before_it_reads`
  持锁方执行 `BEGIN IMMEDIATE` 后**只读不写**，第二个连接在 `busy_timeout=25ms` 下
  `BEGIN IMMEDIATE` **必须失败**（`database is locked`）。
  这正是 `consume_quota`「先读 `used` 再写」所依赖的性质：锁必须在读之前就拿到。
- `test_a_deferred_transaction_only_locks_when_it_writes`
  对照：延迟 `BEGIN` 下只读不写**不持锁**，第二个 `BEGIN IMMEDIATE` 可以成功
  ——这正是必须用 `IMMEDIATE` 的原因。
- `test_the_write_lock_is_released_after_a_rollback`
  回滚后锁释放，后续写事务可正常开始。

**判别力验证**：把持锁方改成延迟 `BEGIN`（只读不写），
`test_an_immediate_transaction_takes_the_lock_before_it_reads` **失败**；
还原后通过。即该测试确实区分 `IMMEDIATE` 与延迟 `BEGIN`，不是恒真断言。

### 5.3 Q1 判定

- 性质「两并发请求不超发硬限」有可执行的通过证据，且败因可归因到限额判定而非锁竞争。
- 机制「读之前即持有写锁」由确定性测试证明，并具备判别力。
- **Q1 解除**，`2.5`（配额 / 允许渠道类型 / 治理停用）可独立落地。

遗留说明（不阻塞）：结果类测试不能证明真实并发下的交错覆盖度，它证明的是
「在 `BEGIN IMMEDIATE` 之下，任何一种交错都不超发」——这一层由 5.2 的机制测试承担。

## 6. 复现命令

```bash
.venv/bin/python -m pytest tests/test_stage2_migration_and_quota_evidence.py -q
# 结果：17 passed
```

## 7. 连带修正

`tests/test_identity_service_writes.py::TenantAdminAccountCreateTests::test_missing_admin_role_rolls_back_account_creation`
在本阶段由通过变为失败：该夹具用**裸 SQL** 删除内置 `tenant_admin` 角色，
而菜单 grant 落地后 `role_resource_grants` 通过外键引用 `roles`，裸删触发外键约束。
生产路径不受影响：`IdentityService.delete_role` 拒绝删除内置角色（`builtin` → 403），
且对自定义角色会在同一事务内先清 grant 再删角色（已有用例覆盖）。
已按生产路径同样顺序修正该夹具（先清 grant 再删角色），其原意（缺角色时账号创建回滚）不变。
