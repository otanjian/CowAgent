# 3.1-3.7 本人定时任务管理

- 记录日期：2026-09-15
- 结论：HTTP、`SchedulerTool` 与后台执行共用同一任务授权服务；五个管理方法在
  3.6/4.3/R1 通过后开放；上游任务编辑与手动运行的断言保留。

## 1. 共用授权服务（任务 3.1）

| 项目 | 事实 |
| --- | --- |
| 唯一实现 | `agent/tools/scheduler/authorization.py`：`TaskActor`、`actor_from_identity_context`（HTTP）、`actor_from_runtime`（工具/后台）、`TaskAccessService` |
| 动作与范围 | `ACTION_VIEW|MANAGE|RUN` × `SCOPE_PERSONAL|PUBLIC`；`decide()` 是纯函数，管理方法、路由拒绝与前端动作投影（`capabilities()`）都取它，页面与服务端不可能各说一套 |
| 个人任务归属 | 创建时从可信身份解析 owner（`tenant_id`+`user_id`）并写 `scope=personal`；选择公共 Agent **不**改变归属 |
| 公共任务 | `scope=public` 单独判定：租户管理员可管理，成员只读；公共判定不延伸到成员私有任务 |
| `self_authorized=True` | 不再作为免检依据：`SchedulerTool` 完全委托 `TaskAccessService`，不直接读写 `TaskStore` |
| 拒绝码 | `not_member / not_owner / agent_not_bound / agent_denied / run_denied / unknown_task / unknown_agent / revision_conflict / quota_exceeded / quarantined / forged_field / already_running / run_unavailable` |

## 2. 列表与五个动作（任务 3.2）

| 项目 | 事实 |
| --- | --- |
| 先授权后聚合 | `list_tasks()` 先解析调用者可见的 Agent 存储集合，再读取、排序、计数与分页；不是先返回全局任务再由前端隐藏 |
| 工具动作 | `create/list/get/delete/enable/disable` 全部经同一服务（`scheduler_tool.py` 只做参数与错误映射） |
| 创建前置 | 可信身份与 owner 解析失败即拒绝（`not_member`），不留下无 owner 任务 |
| 伪造字段 | `FORBIDDEN_PATCH_FIELDS`（owner、tenant、存储路径等）与 `FORBIDDEN_ACTION_FIELDS`（`receiver`、`channel_type`）→ `forged_field`，客户端与工具参数都不能改写服务端归属与通知目标 |
| 详情/运行/启停/更新/删除 | 每次都从真实任务重新解析 tenant/owner/Agent |

## 3. 存储协调与审计（任务 3.3）

| 项目 | 事实 |
| --- | --- |
| revision 原子判断 | `TaskStore.update_task(..., expected_revision=)`，版本不符 → `TaskRevisionConflict` → 拒绝码 `revision_conflict`（HTTP 409） |
| 动作白名单与隐藏字段 | 更新保留未修改的任务字段（通知会话、投递元数据、`silent`、上游扩展字段），只允许登记字段被改写 |
| 多写入者 | `TaskWriteLease` 进程级写租约 + `MultiWriterDeploymentError`：无法协调多写入者的部署拒绝写入，不建第二套任务存储 |
| 审计 | `_audit()` 经 `IdentityService.record_business_audit` 写 `scheduler.<action>`，脱敏载荷只含任务标识/结果；成功与拒绝都记 |
| 配额 | `check_scheduled_task_quota`（`scheduled_tasks` 计量）：HTTP 与工具共用，创建与重新启用都计费，超限 `quota_exceeded` |

## 4. 手动运行（任务 3.4）

| 项目 | 事实 |
| --- | --- |
| 执行身份 | 复用 `agent/tools/scheduler/identity.py` 的 owner 快照/触发重验；运行前重新检查调用者自己的 `agent.use` 与资源授权 |
| 幂等 | 同一 `run_key`（绑定 tenant+member+agent+task）在 600s 内只形成一次有效入队，重试回读已接受结果并记 `duplicate` 审计；任务正在运行时（无 key）返回 `already_running`（409）；运行器不可用返回 `run_unavailable`（503） |
| 入口 | `POST /api/scheduler/run`（payload `run_key`），控制台每次点击生成一个 key；`run_key` 不绑定到任务的工具动作，因为工具不暴露 run |

## 5. 上游行为保全（任务 3.5）

`tests/test_scheduler_web_update.py` 保留上游断言：隐藏通知元数据在编辑后仍在、
切换到 message 模式会丢弃 Agent 专属字段、手动运行委托 scheduler、列表按 Agent 聚合与
作用域、字段白名单。授权补齐做法是补入**合法身份 fixture**（真实 `build_web_app()` 会话），
不删除功能断言。

## 6. 真实入口验收（任务 3.6、R1）

| 用例文件 | 覆盖 |
| --- | --- |
| `tests/test_scheduler_tool_dispatch.py` | 经 `ToolManager` 分发到 `scheduler` 工具的六个动作（真实身份库 + 真实 `TaskStore`）：owner 归属、成员隔离、非 owner 管理员拒绝、撤权成员仍可暂停/删除、配额、跨租户拒绝、跨入口一致 |
| `tests/test_scheduler_task_authorization.py` | 双租户多用户、公共/个人范围、伪造字段、revision 冲突、并发创建、迁移与隔离、审计不含正文、同键幂等运行 |
| `tests/test_scheduler_web_update.py` | 真实路由（会话 + 租户头）的正向编辑/运行、499/503/409 拒绝路径 |
| `tests/test_scheduler_identity_revalidation.py` | 触发前重验：owner 失效、无 owner（`unattributed`）不再执行 |
| `tests/test_task_store_concurrency.py` | 并发写与 revision、写租约拒绝多写入者 |

## 7. 开放（任务 3.7）

`auth/capability_matrix.py` 的 `scheduler` 切片：`list/toggle/update/delete` 为
read/config，`run` 为 execute；`implemented=True, accepted=True`，`reason=""`。
策略为 `tenant`（任务属于一个租户，网关解析会话与租户后把同一上下文交给 handler）。
`scripts/route-baseline.txt` 以追加段登记 5 个方法的策略变化与理由。

## 8. 验证记录

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_scheduler_task_authorization.py -q -p no:randomly` | `26 passed` |
| `.venv/bin/python -m pytest tests/test_scheduler_web_update.py -q -p no:randomly` | `13 passed` |
| `.venv/bin/python -m pytest tests/test_scheduler_tool_dispatch.py tests/test_scheduler_silent.py tests/test_scheduler_identity_revalidation.py tests/test_task_store_concurrency.py -q -p no:randomly` | 见 9.3/12.2 的最终复核记录 |
