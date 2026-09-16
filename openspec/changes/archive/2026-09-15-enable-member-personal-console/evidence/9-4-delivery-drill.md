# 9.4 交付演练证据：幂等升级、中断补偿、关闭开关、停止个人连接与相容版本恢复

- 变更：`enable-member-personal-console`
- 演练用例：`tests/test_personal_delivery_drill.py`（8 项，`_TwoTenantFixture` 双租户夹具）
- 命令：`.venv/bin/pytest tests/test_personal_delivery_drill.py -q` → **8 passed**
- 关联批次：与 `test_personal_capability_switches.py`、`test_tenant_channel_startup_synthesis.py`、
  `test_personal_console_multi_tenant_authorization.py`、`test_tenant_channel_mutations.py`、
  `test_personal_console_acceptance.py` 同批运行 → **87 passed, 4 subtests passed**
- 结论：**通过**。可启用范围与 9.2/9.3 一致（四项能力切片开启；个人渠道执行默认关闭）。

## 1 幂等升级（D1）

`UpgradeIdempotenceDrill.test_d1_reopening_the_database_applies_nothing`

- 对同一 `identity.db` 连续两次构造 `IdentityStore`（等价于一次重启）：
  `schema_migrations` 版本序列完全相同、无重复版本、且等于代码中 `migration_versions()`；
- 同时确认本次变更的列真的在库上：`tenant_channel_instances.scope / owner_user_id /
  governance_disabled_at`。
- 意义：「没有活干」不是掩盖了「库从来没升级过」。

## 2 升级中断可回滚、可重试（D2）

`UpgradeIdempotenceDrill.test_d2_an_interrupted_upgrade_rolls_back_and_retries`

注入一个「先 `CREATE TABLE drill_partial` 再抛异常」的迁移（版本 N+1，不落盘到代码）：

- 失败版本**没有**写进 `schema_migrations`；
- 失败步骤的部分 schema（`drill_partial`）**已回滚**，重开时表集合里不存在；
- 重试（普通一次重启）后版本序列与之前一致，`len(versions) == len(_migrations)`。

原理：`IdentityStore._migrate` 每个版本一个 `BEGIN`，迁移函数抛异常时连接上下文回滚，
版本号与 DDL 在同一个事务里，所以「记录到的版本」必然「schema 已完成」。

## 3 写入中断补偿（D3/D4）

`InterruptedWriteDrill.test_d3_an_interrupted_personal_channel_create_leaves_nothing`

两种中断形态：

- (a) 加密材料不可用（`COW_CREDENTIAL_MASTER_KEY` 缺失）：**事务都没开**，无行、无凭据；
- (b) 更硬的形态：行与密文 bundle 已写、**审计写入失败**。整笔写入是一个
  `BEGIN IMMEDIATE` 事务，`tenant_channel_instances`、`credentials`、
  `credential_versions`、两条 `audit_events` 一起回滚；
- 断言：行数/凭据数与中断前一致、`scope='user'` 为 0；随后重试**像第一次一样成功**
  （不撞唯一索引、不撞「显示名已存在」），恰好 1 行 1 凭据。

`InterruptedWriteDrill.test_d4_an_interrupted_private_agent_create_leaves_no_half_bound_object`

- 克隆/创建工作区抛异常的 `PrivateAgentService`：**不留 binding**
  （`private_owner_user_id` 过滤后为空），也不改 `memberships.default_agent_id`；
- 意义：中断不会留下「指向不存在工作区的私有 Agent」，也不会把成员默认 Agent 挪走。

## 4 关闭开关后的重启形态（D5）

`SwitchWithdrawalDrill.test_d5_withdrawn_switches_keep_the_catalogue_and_the_owner_checks`

以 `member_personal_console / user_private_agent_management / personal_memory_write /
personal_channel_onboarding` 全部关闭重启：

- 五个个人页在 `console_pages` 中均为 `capability_disabled`，`read_allowed=False`；
- **已验收目录不消失**：`personal_channel_types()` 仍返回渠道类型与就绪裁定
  （目录不是私有对象，运营需要看到「若重新打开会提供什么」）；
- **权限检查不被替代**：另一个成员（bob）与平台管理员（root）在
  `list_personal_channel_instances` 上仍看不到 alice 的行，alice 仍看得到自己的行；
- 意义：关开关是「不开放」，不是「不检查」。

## 5 停止个人连接（D6）

`SwitchWithdrawalDrill.test_d6_withdrawing_execution_stops_the_connection`

- 先构造「确实有权连接」的状态：`personal_channel_runtime=True` 且该类型已记入
  真实验收（`PERSONAL_RUNTIME_ACCEPTED_TYPES`），启动合成**包含**该个人实例；
- 再撤下总开关：`personal_runtime_enabled("feishu")` 变为 False；
  `apply_tenant_instance_runtime(instance)` 返回 `applied=False`、
  `error="personal runtime is not enabled for this channel type"`、`pending=True`
  ——即「已保存、未连接」，而不是谎报失败或谎报成功，并且**先停旧连接**；
- 同一状态下再跑一次启动合成：该个人实例**不再出现**，记录到的类型验收
  不能把开关的撤回顶回去。

## 6 相容版本恢复（D7/D8）

`ConsistentRecoveryDrill.test_d7_a_consistent_backup_restores_data_without_starting_personal_instances`

- 造出新数据后做**文件级一致备份**（`shutil.copyfile` 升级后的 `identity.db`），
  在副本上重开 `IdentityService`；
- 副本无需任何补迁移（版本序列与源库一致）；个人渠道行、私有 Agent binding 均保留；
- 副本在 `personal_channel_runtime=False` 下启动合成：**个人实例一个都不启动**
  ——恢复出来的是数据，不是路由；
- 跨租户与管理员的拒绝在恢复后依然成立，owner 依然通过。

`ConsistentRecoveryDrill.test_d8_the_retired_administrator_private_read_is_still_forbidden`

对被移除的旧旁路做直接锚定：`_db_path_owner_forbidden`

- owner → False（可读）；
- 同租户同伴、**声称 `is_tenant_admin` 的上下文**、**声称 `is_platform_admin` 的上下文**
  → 全部 True（拒绝）。该谓词与角色无关，管理员不是例外；
- 同一结论在授权缝合点 `check_resource_action(..., "agent", ..., "read")` 上再次验证，
  避免「只有谓词在挡」。

## 7 变异验证（演练自身会咬）

| 变异 | 位置 | 结果 |
| --- | --- | --- |
| M3 去掉 `apply_tenant_instance_runtime` 的个人运行时闸门（`and False`） | `channel/channel_instances.py` | D6 **失败**（`error` 变空 → 连接未被按开关停下） |
| M4 去掉启动合成的 `scope=='user'` 分支（`and False`） | `channel/channel_instances.py` | D6、D7 **失败**（撤回后/恢复后个人实例被误启动） |
| M5 迁移失败时先 `commit()` 再抛（保留部分 DDL） | `auth/store.py` | D2 **失败**（`drill_partial` 残留） |
| M6 实例行插入后抢先 `con.commit()` | `auth/service.py` | D3 **失败**（审计失败后半成品行留存，行数 1≠0） |
| M7 让声称 `is_tenant_admin` 的上下文绕过私有读 | `channel/web/web_channel.py` | D8 **失败**（旧管理员私有旁路复活） |

五处变异全部被捕获，随后逐条还原，还原后 8 项全过、相关批次 87 项全过。

## 8 运维口径（演练得出的可执行结论）

1. **升级**：可直接就地升级并重启；中断后**直接重启**即可续做，不需要人工清理库。
   版本号与 DDL 同事务，不存在「版本记了、表没建」的中间态。
2. **关闭开关**：改 `config` 五项能力开关后重启即可撤回「开放」；
   已验收目录仍在、owner 检查仍在、成员对自己已有对象的撤回/停用/删除**始终可达**
   （关开关只挡「新开」，不挡「关掉自己已有的」）。
3. **停止个人连接**：撤下 `personal_channel_runtime` 后，热重载路径与启动路径都会停/不拉个人连接；
   记录过的类型验收不能越过总开关。
4. **相容版本恢复（重要）**：**不得**回退到会忽略 `scope='user'`、或会恢复管理员私有读取旁路的
   旧构建。若必须恢复旧版本，只能在维护窗口按**一致备份**恢复，并明确处置本次新增数据
   （个人渠道行、私有 Agent binding、个人记忆根、个人资源参数）。
   本演练通过 D7/D8 锚定「恢复后不得误启动个人实例、不得复活管理员私有读取」这两条不变量。
5. **未通过的切片**：个人渠道**执行**仍默认关闭（无类型完成真实端到端验收，见 7.5 与 9.2），
   因此本 change 的交付范围是「配置可保存、连接不开放」；开启执行需先补齐真实渠道验收。
