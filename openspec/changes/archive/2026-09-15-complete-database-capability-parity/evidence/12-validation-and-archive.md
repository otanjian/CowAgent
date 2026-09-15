# 12. 文档、校验与归档

- 记录日期：2026-09-15
- 结论：**12.1、12.2 已完成**；**12.3 已按“部分归档”执行**（已验收的 7 个 capability 增量合并主规范并归档，
  因外部条件未验收的 Desktop 与微信执行增量移交 `complete-desktop-and-scan-real-acceptance`）；
  **12.4 部分交付**，客户端/提供方真实验收未完成，如实列于下方并由承接方继续，保持未勾选。

## 12.1 交付文档（已完成）

`docs/design/database-capability-parity-delivery.md` 记录"当前版本**实际**支持什么"：

- 逐能力切片的功能/scope/客户端矩阵（`auth/capability_matrix.py` 是唯一真值源，
  文档只是它的可读投影，切片状态变化时同步更新）；
- 10 个恢复入口的策略/来源/授权/handler 与 `scripts/route-baseline.txt` 的可追溯变化；
- 微信提供方清单：**配置面**（扫码绑定 + 保存实例）已交付，**执行面**按 `personal_runtime_enabled()` +
  `PERSONAL_RUNTIME_ACCEPTED_TYPES`（空）保持关闭，且写明关闭原因；
- 个人项目导入与上游 `_import_local_file` 的区别（上游函数尚未落在本树，契约由
  `channel/web/project_import.py` 实现，合并义务记于 `scripts/conflict-baseline.txt`）；
- 历史任务的隔离处理（无主/归属冲突任务被隔离停用，恢复步骤写明）与迁移前备份；
- 部署与恢复说明（分阶段关闭、必需接缝缺失时的行为、回滚）。
- 证据索引表逐组指向本 change（现为 `openspec/changes/archive/2026-09-15-complete-database-capability-parity/`）
  下的 `evidence/` 文件，其中第 6 组已合并为单文件 `6-scoped-project-browser.md`、第 7 组为
  `7-scan-onboarding.md`、第 12 组为本文件。

## 12.2 校验（已完成）

```
$ openspec validate complete-database-capability-parity --strict
Change 'complete-database-capability-parity' is valid

$ .venv/bin/python scripts/check_change_deltas.py complete-database-capability-parity
OK (proposed): complete-database-capability-parity — deltas consistent with the baseline, every conflicted file covered
```

两条命令的语义：前者校验 change 结构、delta 操作与 requirement 格式；后者校验 delta 与当前冲突基线
（`scripts/conflict-baseline.txt`）一致、每个冲突文件都被覆盖。

## 12.3 归档（**已按“部分归档”执行**，2026-09-15）

原任务前置是“完成全部必要依赖和验收”。用户决策把口径改为**按已验收切片归档**：继续把 8 项因外部条件无法
完成的验收挂在同一个 change 上，会让已有真实证据的切片也无法进入主规范，与既有的“按切片验收”治理口径冲突。
因此本次执行部分归档，残余部分整体移交 `complete-desktop-and-scan-real-acceptance`。

**归档前根据外部条件判定未通过的项（不是遗漏）：**

| 阻断 | 原因 |
| --- | --- |
| 任务 7.8 / 7.10 | 真实微信提供方扫码、真实连接与收发需要提供方账号与手机端；本环境不产生该证据，`PERSONAL_RUNTIME_ACCEPTED_TYPES` 保持空集 |
| 任务 8.7 / 8.8 | 真实打包 Desktop 客户端 × 两租户两成员 × 失效身份 × 重连需要 Electron 实机，约束禁止浏览器/Electron 自动化与 `npm install` |
| 门槛 R2 | 依赖上一条（另有远程 HTTPS 形态、兑换响应丢失重试、旧后端拒绝降级未演练） |
| 任务 11.3 | 依赖 7.8-7.10 的真实类型验收 |

**实际归档内容**（`openspec archive complete-database-capability-parity -y` 的真实输出）：

```
Specs to update:
  channel-scan-onboarding: update
  console-navigation-availability: update
  database-memory-console: create
  database-runtime-consumers: update
  database-scheduler-console: create
  fork-upstream-decoupling: update
  scoped-project-browser: create
Totals: + 24, ~ 6, - 0, → 0
Change 'complete-database-capability-parity' archived as '2026-09-15-complete-database-capability-parity'.
```

归档命令同时报告 `Task status: 69/75 tasks` / `Warning: 6 incomplete task(s) found`：6 项未完成任务
（7.8、7.10、8.7、8.8、11.3、R2）**保持未勾选**并留在归档记录里，不是被删除或改写。

**未合并主规范的两块增量（已从本 change 的 `specs/` 移出）**：

- `specs/desktop-tenant-context/**` 全部 6 项 requirement；
- `specs/channel-scan-onboarding/spec.md` 的 ADDED「微信个人执行按实际类型与作用域独立验收」。

二者移入 `openspec/changes/complete-desktop-and-scan-real-acceptance/`，并在移交时补入原前序 R2/8.7 的判据场景
（远程 HTTPS 形态与响应丢失、旧后端拒绝降级、两个租户两个成员与失效身份）。移交只改变承接方，未放宽判据。

**归档后一致性验证（applied 模式）**：

```
$ .venv/bin/python scripts/check_change_deltas.py complete-database-capability-parity
OK (applied): complete-database-capability-parity — deltas consistent with the baseline, every conflicted file covered

$ openspec validate complete-desktop-and-scan-real-acceptance --strict
Change 'complete-desktop-and-scan-real-acceptance' is valid
```

applied 模式的含义：归档目录里保留的每个 ADDED/MODIFIED requirement 与场景都**存在于主规范**，因此
“已归档增量 = 已合并主规范”，移出的两块确实没有进入主规范。

## 12.4 残余范围的承接与运行期表现

| 残余任务 | 承接方任务 | 运行期表现（未变） |
| --- | --- | --- |
| 7.8 / 7.10 | `complete-desktop-and-scan-real-acceptance` 2.1 / 2.2 | 微信个人执行与公共实例个人入口保持关闭，报告“已保存未连接” |
| 8.7 / 8.8 / R2 | 同 change 3.1 / 3.2 / 3.3 | `desktop_tenant_context` 切片 `implemented=True accepted=False reason="awaiting_acceptance"`、`open={}` |
| 11.3 | 同 change 4.1 | 未验收类型/版本/scope 仍关闭 |

## 12.5 交付的真实证据与未完成项（诚实记录）

### 已交付并通过验收（切片已开放）

| 切片 | 状态（实测，2026-09-15） | 主要证据 |
| --- | --- | --- |
| `scheduler` | `enabled=True implemented=True accepted=True open={list:read, toggle:config, update:config, delete:config, run:execute}` | `evidence/3-scheduler-management.md`、`evidence/4-scheduler-task-migration.md`、`evidence/13-review-supplementary-gates.md` §R1 |
| `memory_browse` | `enabled=True implemented=True accepted=True open={list:read, content:read}` | `evidence/5-memory-browse.md`、`evidence/5-memory-console.md` |
| `project_browse` | `enabled=True implemented=True accepted=True open={browse:read, import:execute}` | `evidence/6-scoped-project-browser.md` |
| `weixin_scan` | `enabled=True implemented=True accepted=True open={qr:config, poll:config}`（**只声明 config**） | `evidence/7-scan-onboarding.md`、`evidence/13-review-supplementary-gates.md` §R4 |

（`desktop_tenant_context` 不在此表：它 `implemented=True` 但 `accepted=False`，见下表。）

产品缺陷三处（D1 微信 handler 崩溃、D2 记忆页无拒绝终态、D3 成员经 `/api/agents` 抹除系统供应助理）
均已修复并复验，修复记录在 `evidence/9-route-and-ui-availability.md` §6 与
`evidence/11-plan-3-1-joint-acceptance.md` §4。

### 已交付但**未**通过验收（切片保持关闭 / 任务保持未勾选）

| 任务 | 未完成部分 | 影响 |
| --- | --- | --- |
| `desktop_tenant_context` 切片 | `enabled=False implemented=True accepted=False reason="awaiting_acceptance"`，`open={}` | 路由与消费者保持关闭，理由为未验收而非永久 deferred；`evidence/8-desktop-tenant-context.md` |
| 7.8 | 真实提供方扫码/连接/收发（公共与个人 scope） | `weixin_scan` 只开放 config 动作；个人执行与公共个人入口保持关闭 |
| 7.10 | 按证据更新 `PERSONAL_RUNTIME_ACCEPTED_TYPES` / `PUBLIC_PERSONAL_INGRESS_TYPES` | 两个集合保持空集；配置已保存但执行关闭的投影已断言 |
| 8.7 / 8.8 | 真实打包 Desktop 客户端演练与统一开放矩阵更新 | `desktop_tenant_context` 保持 `accepted=False`；投影与"未验收"一致 |
| R2 | 远程 HTTPS 形态、兑换响应丢失重试、旧后端拒绝降级、真实客户端演练 | 不进入 8.7/8.8 的交付验收 |
| 11.3 | 扫码到个人收发的真实联测 | 第 11 组不宣布全部覆盖；移交承接方 4.1 |
| 12.3 | 归档与主规范合并 | 已按部分归档执行：已验收增量已合并主规范，未验收增量未合并并移交承接方 |

**规划产物完成不等于功能实现完成**：本文件只把上述"已交付并通过验收"的部分记为完成，
所有依赖外部条件或真实客户端的条目保持未勾选，也没有以"关闭即结项"的方式把未验收能力报成可用。

## 12.6 归档后的回归（实测）

**与本次归档直接相关的套件全绿**（归档后运行，即路径已变为
`openspec/changes/archive/2026-09-15-complete-database-capability-parity/`）：

```
$ .venv/bin/python -m pytest tests/test_plan_3_1_joint_acceptance.py tests/test_capability_matrix.py \
    tests/test_doc_edit.py tests/test_personal_console_pages.py tests/test_recovered_entry_acceptance.py \
    tests/test_project_browser.py tests/test_scoped_project_browse.py tests/test_memory_console_scope.py \
    tests/test_scheduler_identity_revalidation.py tests/test_http_policy.py tests/test_route_registry.py \
    tests/test_consumer_closure_acceptance.py -q
300 passed, 19 subtests passed
```

其中 `tests/test_capability_matrix.py::test_every_declared_capability_has_a_spec` 仍通过：`desktop-tenant-context`
的规范移到了承接方 change 的 `specs/`，该用例接受主规范或任一 active change 的 delta。

**全量套件**（`pytest tests/ -q`，4103 passed / 43 failed）的 43 个失败与本此归档无关，逐项核对如下：

| 失败文件 | 数量 | 原因 | 与本次归档的关系 |
| --- | --- | --- | --- |
| `test_security_ssrf_browser_navigate.py` | 12 | 浏览器工具未就绪（`Browser tool not ready`），环境依赖 | 无（归档前既有） |
| `test_user_avatar.py` | 8 | 头像读取路由未返回 404/期望状态，属工作区既有未提交改动 | 无 |
| `test_read_edit_improvements.py` | 3 | `ModuleNotFoundError: No module named 'pypdf'`，环境缺可选依赖 | 无 |
| `test_project_db_containment.py`、`test_project_db_isolation.py` | 5 | `shared_root` monkeypatch 签名（既有 5 处同源缺陷） | 无 |
| `test_personal_console_menu.py` | 2 | 页面投影要求资源授权（`admin.memory` 需 readable Agent），既有改动 | 无 |
| `test_execution_authorization_fail_closed.py`、`test_agent_workbench.py` | 3 | legacy 模式期望与"database 为唯一身份模式"的既有改动不一致 | 无 |
| `test_feishu_*`、`test_dashscope_provider.py`、`test_claude_thinking.py`、`test_identity_credential.py`、`test_http_gate.py`、`test_trajectory_eval.py` | 10 | 提供方/网关/凭据/隔离边界的既有失败（含网络与凭据环境） | 无 |

判定依据：上述 15 个失败文件**没有任何一个**引用 `openspec/`，也不读取本归档目录或
`auth/capability_matrix.py`（`rg -l "openspec" <失败文件>` 为空）；本次归档只移动了 OpenSpec 目录、
改写了本 change 的规范/任务/证据与交付文档，并更新了一处测试 docstring 与一处注册表注释，未改动任何
运行时代码路径。因此这 43 项是工作区既有或环境依赖的失败，不构成本次归档的回归。
