# 10.1-10.6 master 更新后合入 rdai 的保全与回归

- 记录日期：2026-09-15
- 结论：在隔离克隆中完成一次真实 merge 排练，结果与冲突基线逐文件一致（无新增漂移）；
  共享文件按登记接缝处理；语义回归（路由覆盖、上游接缝、会话 schema、fork 片段、调度
  Web 行为、旧鉴权不复活）全部通过；上游漂移用例已加入。

## 1. 源、目标与 merge-base（任务 10.1）

| 项目 | 取值 |
| --- | --- |
| 源 master | `origin/master@9ad944dd3d94ee0fcf8b93e7f2c23e67b5a72226`（2026-09-09T18:07:11+08:00，2026-09-15 用 `git ls-remote` 复核远端仍是该提交） |
| 目标 rdai（本地实施候选） | `rdai@e6f1e2a7cb4a6147cd125e7007a1c5982521d76c`（2026-09-14T19:04:28+08:00，含本 change 的未提交改动） |
| 已推送的 rdai | `origin/rdai@3707b28f690bf654a5fb24f038a09157b147f415`（落后本地实施候选） |
| merge-base | `e5e2a52d3f309130ebabcb69ee0fb12adf754fbd` |

`scripts/conflict-baseline.txt` 头的 `origin/master@9ad944dd x origin/rdai@617abfae` 只作为
历史冻结记录；本轮以实测 ref 为准，并已在基线头部登记。

## 2. 排练（任务 10.4）

| 项目 | 事实 |
| --- | --- |
| 隔离环境 | `git clone --no-hardlinks . /tmp/caprehearsal`（只含已提交历史，不触碰工作区） |
| 命令 | `PYTHON=<repo>/.venv/bin/python bash scripts/sync-from-master.sh upstream master`（脚本会 fetch + 合并 + **中止**，不提交不推送） |
| 结果 | 21 个冲突文件，全部被报告为「已知冲突」，无新增/消失文件；`README.md`、`docs/zh/README.md`、`docs/zh/README-Hant.md`、`docs/ja/README.md`、`PermissionSelector.tsx` 五个 modify/delete 决策与基线一致 |
| 结论 | 上游 master 自冻结基线以来未移动，本轮没有需要新分类的上游路由/方法/任务字段/传输/启动点变化 |
| 候选差异的处理 | 排练基于已提交 HEAD，本轮工作实际位于工作区未提交；因此在 `seam:` 列追加本 change 的任务归属（见下），而不是新增冲突行 |

基线逐文件新增的任务归属（`scripts/conflict-baseline.txt`）：

| 文件 | seam 归属（本轮追加） |
| --- | --- |
| `agent/tools/scheduler/integration.py` | `,3.4`（唯一执行身份与触发重验消费方） |
| `app.py` | `,2.3,4.1-4.3`（新迁移 hook 只转调） |
| `channel/channel_instances.py` | `,7.3`（按显式实例读取密文与连接状态） |
| `channel/web/chat.html` | `,2.2,9.1`（fragment 挂载点契约） |
| `channel/web/static/js/console.js` | `,2.2,9.1`（动作投影渲染） |
| `channel/web/web_channel.py` | `,3.1-3.7,5.1-5.4,6.1-6.4,7.1-7.7`（四个切片的 handler 适配） |
| `desktop/src/renderer/src/api/client.ts` | `,8.1,8.4`（统一上下文装饰器） |
| `tests/test_scheduler_web_update.py` | `,3.4,3.5`（保留上游断言 + 补合法身份 fixture） |

新增 fork 自有模块（`auth/capability_matrix.py`、`channel/web/scan_onboarding.py`、
`channel/web/memory_console.py`、`channel/web/project_import.py`）在上游无对应文件，不可能
冲突；若上游将来新增同名路径，合并必须失败关闭并分类，不能静默合并出「一个问题两个答案」。

## 3. 逐接缝保全（任务 10.2）

| 共享文件 | 保全要求 | 本轮处理 |
| --- | --- | --- |
| `agent/memory/conversation_store.py` | 组合 schema、租户查询接缝 | 未改其 schema；本 change 只改 `agent/memory/personal.py`、`manager.py`、`summarizer.py` 的路径与版本协议 |
| `app.py` | 保留上游启动步骤 | 只保留 `_migrate_scheduled_tasks()` 转调，以及 R5 的 `_verify_required_seams()` 具名入口（直接调用清单校验，不经 hook） |
| `common/startup_hooks.py` | 具名 hook 登记 | 新增 `HOOK_SCHEDULER_TASK_MIGRATION` 与 `REQUIRED_HOOKS` 清单/校验，不改既有 hook 语义 |
| `agent/tools/scheduler/integration.py`、`identity.py` | 唯一执行身份 | 复用未替换 |
| `channel/web/chat.html`、`console.js` | fragment 契约、上游交互 | 只改定时卡片渲染与 `run_key` 传参 |
| `channel/web/web_channel.py` | 上游本机导入契约（`_import_local_file` 尚未落入本树） | 不整文件覆盖；导入预览/发布分离到 `channel/web/project_import.py`，上游落地后按接缝覆盖清单验证其 loopback/令牌校验与本次实现并存 |
| `desktop/src/renderer/src/api/client.ts` | 保留上游业务方法签名 | 用统一上下文装饰器接入，不逐方法加分支 |
| 渠道逐类型验收、审批消费接缝 | 逐类型登记 | 渠道实例字段按类型登记；审批消费接缝在 7.9 记录 |

## 4. 语义回归（任务 10.3）

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_upstream_core_seams.py tests/test_no_resurrection_legacy_identity.py tests/test_conversation_schema_seam.py tests/test_scheduler_identity_seam.py tests/test_startup_hook_seam.py tests/test_channel_signature_seam.py tests/test_route_registry.py -q -p no:randomly` | `90 passed, 2 skipped`（R5 补强后含新增的必需接缝清单用例） |
| `node --test tests/test_fork_fragments.cjs` | `6 passed / 0 failed`（fragment 挂载机制未改；含 R5 的「仅可选 UI 接缝缺失只回退展示」用例） |
| 10 个恢复入口的真实授权回归 | 见 `evidence/9-*.md`（`tests/test_recovered_entry_acceptance.py`） |

## 5. 上游漂移用例（任务 10.5）

`tests/test_upstream_drift_guards.py`：

| 漂移 | 断言 |
| --- | --- |
| 上游为已恢复 URL 新增 HTTP 方法 | 追加第二条同 pattern 条目被拒绝（`duplicate route pattern`）；新方法缺策略/未知策略被 `_validate_entry` 拒绝；handler 实现但未登记的方法由 `check_route_coverage` 报出 |
| 上游新增任务字段（任务级与 `action` 内） | 经真实控制台编辑后字段原样保留；`owner`/`tenant_id`/`receiver` 仍不可伪造（`forged_field`） |
| 上游新增 Desktop 传输 | 由 `tests/test_desktop_auth_flow.py` 与 client 装饰器接缝覆盖（见 `evidence/8-*.md`） |
| 上游新增记忆索引/固化发布入口 | 由 `evidence/5-*.md` 的发布版本协议用例覆盖 |
| 上游新增渠道动作分发 | 由审批消费接缝用例覆盖（见 `evidence/7-*.md`） |

未分类变化必须阻止验收：注册表校验 + 覆盖不变量 + 上述用例共同构成门槛。

## 6. 后续更新执行说明（任务 10.6）

每次 master → rdai 更新按下列顺序执行，并记录源/目标/merge-base 与证据版本：

1. `git ls-remote <upstream> refs/heads/master` 记录真实上游提交，不使用历史冻结哈希；
2. 在隔离检出（`git clone --no-hardlinks` 或 worktree）执行 `scripts/sync-from-master.sh <remote> master`，保存完整报告；
3. 按报告逐文件对照 `scripts/conflict-baseline.txt`：新增/消失的冲突文件必须先决策（`seam:<任务>` / `keep-fork` / `merge-docs` / `keep-deletion`）再合并；
4. 对解决冲突后的最终候选运行：路由覆盖与策略不变量、上游接缝、会话 schema、fork 片段、调度 Web 行为、旧鉴权不复活、10 个恢复入口的正向调用与拒绝矩阵、Desktop 上下文；
5. 新路由/方法/任务字段/传输未分类时阻止验收；
6. 记录本轮候选 commit 与证据文件，再进入下一轮。
