# 4.1-4.3 历史任务与数据迁移

- 记录日期：2026-09-15
- 结论：历史任务按可信证据补齐 owner/scope，无法证明归属的隔离停用；迁移幂等、可重跑、
  可中断恢复；迁移不把任务归给当前管理员，也不恢复 legacy 无身份执行。

## 1. 迁移实现（任务 4.1-4.2）

| 项目 | 事实 |
| --- | --- |
| 实现入口 | `TaskAccessService.migrate_tasks(actor, agent_ids=None, apply=False)`（`agent/tools/scheduler/authorization.py`）+ 启动 hook `HOOK_SCHEDULER_TASK_MIGRATION`（`common/startup_hooks.py::_scheduler_task_migration`） |
| `app.py` 角色 | 只转调：`_migrate_scheduled_tasks()` → `run_startup_hook(HOOK_SCHEDULER_TASK_MIGRATION)`，无内联迁移逻辑 |
| 保留内容 | 任务 ID、`schedule`、`action`、通知会话与接收目标、`silent`、未知上游字段全部保留，只追加 owner/scope/revision 兼容元数据 |
| 归属回填 | 仅在任务已带可信 owner（用户 与 租户）时标记 `scope=personal`；不按当前管理员、默认 Agent 或文件路径猜 owner |
| 无归属任务 | 无 owner 且无显式 scope 的历史任务被隔离：保留正文，写入隔离标记并停用（`enabled=False`），运行路径以 `quarantined` 拒绝 |
| 冲突任务 | 归属冲突（owner 指向不存在/停用成员）同样进入隔离，不归给当前执行迁移的管理员 |
| 幂等 | 重复运行不重复写、不改变已完备任务；部分完成的中断可重跑，已处理任务不重复计数 |
| 不可读存储 | 某个 Agent 的 `tasks.json` 不可读时跳过并继续，不因单文件失败中断整个启动 |

## 2. 演练与拒绝（任务 4.3）

| 演练 | 断言 |
| --- | --- |
| 中断后重跑 | 迁移在中途中断后再次运行，结果与一次运行一致（`stamped`/`quarantined` 计数不重复） |
| 不归给当前管理员 | 运行迁移的管理员不会成为历史任务的 owner |
| 不恢复 legacy 执行 | 隔离任务即使被手工重新启用，运行路径仍以 `quarantined` 拒绝（`tests/test_scheduler_task_migration.py::test_a_quarantined_task_never_delivers`） |
| 无 owner 任务的运行 | `agent/tools/scheduler/identity.py::revalidate_owner` 在 database 身份模式下对无 owner 任务返回 `UNATTRIBUTED`，后台执行跳过（`tests/test_scheduler_identity_revalidation.py`） |
| 备份证据 | 启动 hook 先做一次 dry run，对「计划中要改动的」每个 Agent 存储写一份一致快照 `<tasks.json>.bak-pre-migration-<时间戳>`（模式 0600，每存储保留 5 份，dry run 显示无需改动时不写）；备份在启动单线程阶段、任何定时器启动前完成，因此是迁移前的真实状态。用例：`tests/test_scheduler_task_migration.py::test_the_boot_hook_backs_up_the_store_before_it_writes`（断言快照中无 `scope` 且任务仍启用，第二次启动不再产生备份） |

## 3. 数据边界

| 项目 | 决定 |
| --- | --- |
| 迁移号 | 不新增 `auth/store.py` 迁移（调度 owner/scope 是 `tasks.json` 的兼容字段） |
| 第二套存储 | 不建：任务仍在原 `TaskStore`（每 Agent 一个 `tasks.json`） |
| 多写入者 | `TaskWriteLease` 拒绝无法协调的部署，迁移与 HTTP/工具/后台共用同一写协调 |
| legacy 回退 | 运行时不提供回到无身份执行的开关；`HOOK_IDENTITY_MODE_CONSISTENCY` 在身份模式不一致时中止启动 |

## 4. 验证记录

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_scheduler_task_migration.py tests/test_scheduler_identity_revalidation.py -q -p no:randomly` | 见 9.3/12.2 的最终复核记录（迁移用例覆盖中断重跑、隔离停用、幂等、不可读存储、隔离任务不执行） |
| `.venv/bin/python -m pytest tests/test_task_store_concurrency.py -q -p no:randomly` | 见 9.3/12.2 的最终复核记录（revision 与写租约） |
