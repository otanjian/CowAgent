# 5.1-5.8 记忆浏览兼容入口与共享记忆服务补强

- 记录日期：2026-09-15
- 结论：两个旧记忆读接口以**显式目标 + 先判归属后读**的兼容入口恢复；共享个人记忆服务补上
  「使用时校验的真实归属」与「正文/索引/清空共用操作版本」，清空后旧正文与旧索引都不会复活；
  两次接口均为读，写入仍走既有 `/api/memory/personal`。

## 1. 兼容入口（任务 5.1-5.4）

实现：`channel/web/memory_console.py`（fork 自有模块，上游 `MemoryHandler` 保持一次 `try` / 一次
`except` 的形状，只把它委托给这个接缝）。注册：`auth/capability_matrix.py` 的 `memory_browse`
切片（`list`/`content` 均为 `read`，策略 `tenant` + 权限 `memory.read`），
经 `channel/web/route_registry.py` 的 `S("memory_browse", ...)` 派生；`scripts/route-baseline.txt`
的 evolution 段记录旧值 `closed` 与新值。

三个**显式**目标，取不到唯一答案就拒绝：

| 目标 | 判据 | 归属 |
| --- | --- | --- |
| `personal` | 调用者自己的 `(tenant, user)` 域 | `PersonalMemoryService`（唯一 CRUD，未复制） |
| `private_agent` | 私有 Agent | `_require_private_owner` 在读**之前**判定；管理员不是例外 |
| `shared` | 租户共享 Agent 记忆 | 同租户 + `memory.read` |

稳定拒绝码（HTTP 状态是拒绝类别，body 的 `code` 是机器可判定的字符串）：
`unknown_scope`、`ambiguous_target`（`scope=personal` 与 `agent_id` 同时给出）、
`unknown_category`、`unknown_agent`、`unknown_entry`、`not_owner`、`unsafe_path`。
`personal` 域**不**回退到租户共享根（设计 D5）：没有个人记忆就是空列表，不是租户的文件。
跨租户**没有**单独的码：他人的 Agent 与不存在返回同样的 `unknown_agent`/404（不泄露存在性，
与 `tests/test_tenant_read_scoping.py` 的既有口径一致）；外租户成员收到的 403 来自 HTTP 闸门
（该租户选择本身无成员关系），不是本模块。

个人域条目用 `id`/`filename` 寻址（相对**个人根** `<tenant>/users/<user_id>`，不是工作区根），
响应带 `read_only: true`，因为写入动词在 `/api/memory/personal`。列表分页上限 `MAX_PAGE_SIZE = 200`。

## 2. 共享记忆服务的路径归属（任务 5.6）

`agent/memory/personal.py` 的入口只接受相对标识，解析与读写全部经 `common/safe_fs.py` 的锚定访问
（逐段 `O_NOFOLLOW`、校验过的目录描述符即实际使用的目录），所以「先检查后替换」不能把操作重定向到根外。
覆盖枚举、元数据、读取、保存、删除、清空六个动作，条目软链接、中间目录软链接、根软链接一律拒绝。

## 3. 正文、索引与清空的版本协调（任务 5.7）

作用域状态（`.memory-scope.json`）保存单调递增的 `op_version`（每次保存/删除/清空 +1）与
`generation`（仅清空 +1）；变更在提交正文时**先**登记发布意图，索引发布前后都重新校验版本：

- 版本已变的发布既不写回旧内容，也不删除较新版本的索引（`test_an_overtaken_publisher_does_not_write_rows_back`、
  `test_an_overtaken_publisher_does_not_purge_a_newer_version`）；
- `save` 释放正文锁之后索引可能复活的窗口被关闭：`test_a_save_in_flight_cannot_revive_content_after_a_clear`；
- 发布意图**持久化**，进程中断后 `recover_incomplete_publish` 把它提升为待重试并继续屏蔽，
  重启不会把未完成的发布当成功（`InterruptedPublishTests`）；
- 作用域标记损坏时降级为「不隐藏记忆」而不是不可用（`test_a_corrupt_scope_marker_degrades_instead_of_hiding_memory`）；
- 索引标签与 `MemoryManager.sync` 使用同一套（`memory/users/<user_id>/...`），
  `SyncPublishSeamTests` 证明丢失作用域版本的同步会丢弃该用户域的工作。
- 只有进程内协调：多写者部署不安全，这一点在 `personal.py` 的模块文档「单写者约束」中明确禁止。

## 4. 与个人记忆 CRUD 的联合边界（任务 5.5）

- 新兼容入口与已开放 `/api/memory/personal*` 共用同一个 `PersonalMemoryService`（`test_the_personal_endpoints_still_behave`、
  `test_the_compat_personal_scope_does_not_expose_another_member`）；
- 编辑/删除/清空的版本条件与索引恢复由同一套 `op_version`/`generation` 覆盖
  （`tests/test_personal_memory_console.py`、`tests/test_personal_memory_tool_execution.py`）；
- 旧任务防回写：`agent/memory/summarizer.py` 在写入出口复核版本，清空前派发的固化任务被拒
  （`test_recovery_never_reports_the_interrupted_publish_as_complete` 与 `SyncPublishSeamTests`）。
- 其他用户与共享记忆不受影响：`test_other_users_and_shared_memory_are_untouched_by_a_clear`。

## 5. 验证证据

命令与真实结果：

```
.venv/bin/python -m pytest tests/test_memory_console_scope.py tests/test_personal_memory_scope_protocol.py \
    tests/test_memory_storage_tenant_scope.py tests/test_personal_memory_console.py \
    tests/test_personal_memory_tool_execution.py tests/test_user_personal_memory.py -q -p no:randomly
→ 115 passed
```

关键用例：

| 任务 | 用例 |
| --- | --- |
| 5.1 / 5.2 | `test_a_member_lists_and_reads_their_own_personal_memory`、`test_the_legacy_shape_without_a_scope_reads_my_own_personal_memory`、`test_a_missing_scope_with_an_agent_reads_that_agents_memory`、`test_the_owner_reads_their_private_agents_memory` |
| 5.2（管理员不是例外） | `test_a_non_owner_is_refused_before_any_read`（私有记忆服务被替换为 tripwire，证明「拒绝先于读取」）、`test_the_legacy_agent_id_shape_keeps_the_same_owner_gate` |
| 5.3（跨租户/非法目标） | `test_a_member_of_another_tenant_is_refused`、`test_another_tenants_agent_is_not_addressable`、`test_the_same_account_bound_to_another_tenant_reads_only_its_own`、`test_an_unknown_scope_is_refused`、`test_an_unknown_category_is_refused`、`test_the_personal_scope_refuses_an_agent_target`、`test_an_unknown_entry_is_refused` |
| 5.3（软链接与替换） | `test_an_entry_symlink_out_of_the_root_is_refused`、`test_an_intermediate_directory_symlink_is_refused`、`test_a_symlinked_personal_root_is_refused`、`test_a_symlinked_agent_memory_directory_is_not_followed`、`test_a_symlinked_agent_entry_is_not_served`，以及 `test_replacing_the_directory_after_listing_does_not_leak_content` |
| 5.4（开放登记一致） | `test_the_capability_registry_and_the_route_table_agree`、`tests/test_recovered_entry_acceptance.py` |
| 5.6 | `PathOwnershipTests` 全部 5 例 |
| 5.7 / 5.8 | `CommitPublishAtomicityTests`、`InterruptedPublishTests`、`SyncPublishSeamTests` 全部 11 例 |

## 6. 未覆盖与边界

- `dreams/` 与 `evolution/` 日志**有意不受清空影响**（非用户撰写的条目），这是 5.4 的明确边界，不是缺口；
  个人域的 `category` 因此只允许 `memory`。
- 索引层（`chunks`/FTS5/向量）没有墓碑，清空后的屏蔽依赖 `pending_index` 标签与发布版本复核，不是索引删除；
  任何新读入口都必须复用该机制，不得直读索引。
