# 2.1-2.4 接缝与唯一能力登记

- 记录日期：2026-09-15
- 结论：10 个既有路由使用「原 handler + 切片派生策略」的显式适配，没有复制第二份路由表、
  没有追加同名 URL 遮蔽；前端新增独立模块并复用既有 `[data-fork-fragment]` 挂载契约；
  迁移与启动检查只经 `common/startup_hooks.py` 的具名 hook，`app.py` 保持转调；唯一的
  切片登记模块 `auth/capability_matrix.py` 同时供路由、消费者投影与页面投影读取。

## 1. 路由接缝（任务 2.1）

| 项目 | 事实 |
| --- | --- |
| 注册方式 | 单表 `channel/web/route_registry.py::ROUTES`，条目形如 `RouteEntry(pattern, handler, source, methods)`；`source` 标记 `upstream` 或 `fork:<任务/主题>` |
| URL 表来源 | `derive_web_urls()` 扁平化为 `web.application` 的 `(pattern, handler)`，`build_web_app()` 只消费该派生结果 |
| 策略表来源 | `derive_route_policy()` 扁平化为 `{pattern: {METHOD: entry}}`，`auth/http_policy.py` 的 `ROUTE_POLICY` 直接取它；`tests/test_route_registry.py::test_both_tables_are_derived_from_the_registry` 锁定 |
| 重复 URL | `derive_route_policy()` 遇到重复 pattern 抛错（`tests/test_route_registry.py::test_no_duplicate_patterns`），本轮 10 个方法复用原 pattern，不新增 URL、不追加遮蔽条目 |
| 来源与方法集合检查 | `_validate_entry` 校验 pattern/ handler/ source/ methods/ policy 合法；`check_route_coverage(vars(web_channel))` 校验「注册的 pattern 与方法」与「handler 实际实现的方法」两腿一致，当前为 `[]` |
| 本轮 10 个方法的适配 | 仍是原 upstream handler（`SchedulerHandler`、`SchedulerRun/Toggle/Update/DeleteHandler`、`MemoryHandler`、`MemoryContentHandler`、`WeixinQrHandler`、`ProjectBrowseHandler`），策略改为 `S(<slice>, <action>)`，未新增 handler 类 |

未新增替换注册器：注册表本身支持「指定 URL 的 handler/策略来自哪一块」的表达，10 个方法的
策略来源从字面量 `closed` 改为切片登记，属于登记内改动而非第二张表。

## 2. 前端接缝（任务 2.2）

| 项目 | 事实 |
| --- | --- |
| 挂载契约 | `channel/web/static/js/fragments.js` 查询 `[data-fork-fragment]`、fetch 注入片段、重跑 `applyI18n()`、派发 `fork-fragment-mounted`；由归档变更 `fork-decoupling-and-tenant-hardening` 建立，`tests/test_fork_fragments.cjs` 锁定 |
| 独立功能模块 | `static/js/personal-console.js`（个人控制台视图）与其 i18n 模块 `static/js/i18n/personal-console.js` 为 fork 自有模块；定时/记忆/项目/扫码按同一形态增加模块与挂载点 |
| `console.js` 的最小改动 | 本轮只改定时卡片的动作渲染：每个任务的动作从服务端投影 `task.capabilities`（由 `TaskAccessService.decide()` 派生）取值，并显示 `task.scope` 归属芯片；不再无条件渲染「立即运行/启停」按钮，无权限时不渲染而非渲染后 403 |
| `chat.html` | 本轮不改结构；上游聊天、上传与任务编辑行为保留（`tests/test_agent_workbench_frontend.cjs`、`tests/test_console_view_registry.cjs` 覆盖） |
| 上游交互保留 | 任务编辑弹窗、隐藏通知元数据、手动运行等断言仍在 `tests/test_scheduler_web_update.py` 内并保持通过（任务 3.5） |

## 3. 迁移与启动接缝（任务 2.3）

| 项目 | 事实 |
| --- | --- |
| 具名 hook 登记 | `common/startup_hooks.py`：`HOOK_IDENTITY_MODE_CONSISTENCY`、`HOOK_DATABASE_BOOTSTRAP`、`HOOK_TENANT_CONVERSATION_BACKFILL`，本轮新增 `HOOK_SCHEDULER_TASK_MIGRATION` |
| `app.py` 的角色 | 只保留转调：`_guard_identity_mode_consistency()`、`_ensure_database_bootstrap()`、`_tenant_conversation_backfill()`、`_migrate_scheduled_tasks()` 各自 `run_startup_hook(<name>)`，无内联迁移逻辑 |
| 缺失接缝的处置 | 未注册时 `run_startup_hook` 返回 False，上游启动序列不变；身份模式守卫在 hook 抛错时**中止启动**（registry 不吞异常），不允许带旧身份继续运行 |
| 不运行旧无身份路径 | 强制接缝缺失时，受影响的消费者在路由层由 `closed` → 503 拒绝（`auth/http_policy.py::enforce_http_policy`），identity 解析异常时按 `_gate_fail_closed()` 失败关闭 |
| 本轮新增迁移 | `_scheduler_task_migration`（任务 4.1-4.3）：幂等补齐历史任务的 owner/scope，无法证明归属的隔离停用；无重启副作用 |

## 4. 唯一切片登记（任务 2.4）

| 项目 | 事实 |
| --- | --- |
| 登记模块 | `auth/capability_matrix.py`：每个切片声明 `implemented/accepted/open(action->read\|config\|execute)/scope/reason/policy/permission` |
| 路由消费 | `channel/web/route_registry.py::S(slice, action)` → `capability_matrix.route()`；动作未开放时返回 `closed` 条目 |
| 消费者投影 | `IdentityService._consumer_availability()` 合并 `capability_matrix.consumer_availability()`，仓库内不再另写这些消费者的可用性 |
| 页面投影 | `capability_matrix.page_availability()` 由同一切片推导；关闭页面报告切片自身的 `reason`，不写死 `deferred` |
| 不一致即失败 | `check_consistency()` 强制：未实现不得开放动作、`execute` 无真实验收不得开放、未知 access 类拒绝 |
| 缺失接缝拒绝测试 | `tests/test_capability_matrix.py`（12 项）：声明一致性、切片 id/consumer 唯一、声明的 capability 必须存在、未实现开放/无验收 execute/未知 access 三类探针必须被 `check_consistency` 拒绝、10 个恢复方法的策略必须逐一等于切片派生结果、关闭动作必须仍是 `closed`、三条投影（路由/消费者/页面）互不矛盾、关闭页面报告自身原因 |

## 5. 验证记录

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_capability_matrix.py -q -p no:randomly` | `19 passed`（含页面 read/config/execute 投影与「开放切片不等于把页面交给每个人」用例） |
| `.venv/bin/python -m pytest tests/test_route_registry.py -q -p no:randomly` | 见 9.3 记录（含 `check_route_coverage(vars(web_channel)) == []`） |
| `.venv/bin/python -m pytest tests/test_http_policy.py -q -p no:randomly` | 见 9.3 记录（含关闭消费者 503 与管理员不可覆写、恢复面 401 用例） |
