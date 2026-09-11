## ADDED Requirements

### Requirement: legacy 面退役的删除决策入冲突基线

删除上游原生的 `legacy` 认证面（`web_password`、`cow_auth_token`、`AuthLoginHandler`/`AuthLogoutHandler`/`AuthCheckHandler`、`_get_web_password`/`_create_auth_token`/`_verify_auth_token`/`_check_auth`/`_require_auth`，以及 `route_registry.py` 中登记为 `upstream` 的三条 `/auth/*` 路由）SHALL 作为**长期删除决策**写入 `scripts/conflict-baseline.txt`。因本退役是**文件内删除/改写**（冲突形态为 `UU`）而非整文件删除（`DU`），逐文件处置 SHALL 使用 `keep-fork` 或 `seam:<tasks>`，文档双侧编辑使用 `merge-docs`；MUST NOT 对本 change 的文件内删除使用 `keep-deletion`，MUST NOT 因此改动 `scripts/sync_report.py` 的 `DELIBERATE_REMOVALS`。处置决策 SHALL 对上游后续同文件的改动持续生效。系统 MUST NOT 在每次合并时重新人工裁决该删除，MUST NOT 因合并静默复活共享密码或旧 token 认证路径。

#### Scenario: 上游继续修改被删认证文件
- **WHEN** 合并发现 `channel/web/web_channel.py` 或 `channel/web/static/js/console.js` 在 fork 侧删除 legacy 认证块、在上游侧被修改
- **THEN** 合并按基线登记的 `keep-fork` 或 `seam:<tasks>` 处置取舍，不复活 `web_password`、`cow_auth_token` 或 `/auth/*` 共享密码路径

#### Scenario: 上游新增路由被登记而非静默挂载
- **WHEN** 合并引入新的 `/auth/*` 或认证相关路由
- **THEN** 该路由必须出现在权威清单与授权策略表中方可可达，不得以未登记方式挂载

#### Scenario: 不误用整文件删除词表
- **WHEN** 本 change 产生的冲突全部为文件内改写（`UU`）
- **THEN** 基线登记为 `keep-fork`/`seam:`/`merge-docs`，`DELIBERATE_REMOVALS` 与既有 `keep-deletion` 行保持不变且仍镜像一致

### Requirement: 退役后缺失断言式回归防止旧认证复活

对于上游可能重新引入的 legacy 认证符号（共享密码字段、旧 token cookie、旧认证 handler、`_require_auth`、免登录分支），系统 SHALL 提供可执行的「不复活」断言式回归，明确断言这些符号**不存在**于实现中；MUST NOT 仅在文档中声明已删除。该回归 SHALL 在合并上游后立即运行，使旧认证路径的任何回归以测试失败而非静默放行暴露。

#### Scenario: 合并复活共享密码字段
- **WHEN** 一次上游合并把 `web_password` 或 `cow_auth_token` 重新写回实现
- **THEN** 不复活回归失败并指出被复活的符号，构建不得视为通过

#### Scenario: 合并复活旧登录 handler
- **WHEN** 一次上游合并重新引入 legacy `/auth/login` handler、`_verify_auth_token` 或 `_require_auth`
- **THEN** 不复活回归与路由覆盖不变量同时失败，指出该 handler 与未登记路由

### Requirement: 退役批次完成后立即同步排练并登记基线

删除上游原生认证面的工作 SHALL 收拢为紧凑批次（含 handler 级 `_require_auth`/`ctx is None` 收敛），并在批次完成后 SHALL 立即以 `scripts/sync-from-master.sh` 对当时 `master` 做一次同步排练，用结果重新生成 `scripts/conflict-baseline.txt` 并按 `keep-fork`/`seam:`/`merge-docs` 登记新增的修改类冲突处置；MUST NOT 在未排练、未登记基线的状态下把该批次视为完成。

#### Scenario: 批次后同步排练
- **WHEN** 删除批次落地，且工作树干净
- **THEN** 同步排练产出冲突报告，新增冲突文件逐条登记处置（文件内改写为 `keep-fork`/`seam:`，文档为 `merge-docs`），报告无未决项

#### Scenario: 未登记基线不得收尾
- **WHEN** 批次声称完成但 `conflict-baseline.txt` 未包含新产生的修改类冲突
- **THEN** 验收失败，`scripts/check_change_deltas.py` 报告存在未被本 change 点名的冲突文件

### Requirement: 上游资产与接缝在退役中保持不丢失

本 change 的安全删除 SHALL NOT 触碰或退化既有上游资产与接缝：会话存储组合 schema（`agent/memory/conversation_store.py`）、调度身份收敛接缝（`agent/tools/scheduler/integration.py`）、`chat.html` 的 `data-fork-fragment` 挂载点、`tests/test_scheduler_web_update.py` 的上游行为断言，以及上游 `_import_local_file` 的 loopback 与每启动令牌校验。这些接缝的既有测试 SHALL 保持通过，MUST NOT 因删除 legacy 而被移除或放宽。

#### Scenario: 退役后上游接缝测试仍通过
- **WHEN** 删除批次落地后运行既有接缝测试
- **THEN** 组合 schema、调度身份收敛、fork fragment 挂载与本地导入保全校验全部通过，未出现被删除或放宽的断言

#### Scenario: 本地导入保全未被静默丢弃
- **WHEN** 合并把上游 `_import_local_file` 带入本树
- **THEN** 其 loopback 与每启动令牌校验完整保留，除非另有显式登记的决策将其移除
