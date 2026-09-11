# 验收证据：tenant-tabbed-editor

本文件记录本次改动的命令、原始输出与结论，并明确区分「已验收」与「未测试」。
所有命令均在工作区 `/Users/jiantan/ai_assistant/cowagent` 下执行。

## 1. 前端用例（`tests/test_tenant_*.cjs` 与身份/导航相关）

```
$ node --test tests/test_tenant_admin_account_picker.cjs tests/test_tenant_create_frontend.cjs \
      tests/test_tenant_tabbed_editor_frontend.cjs tests/test_identity_admin_frontend.cjs \
      tests/test_nav_area_frontend.cjs tests/test_sidebar_account_frontend.cjs
ℹ tests 94
ℹ pass 94
ℹ fail 0
```

`tests/test_tenant_tabbed_editor_frontend.cjs` 覆盖 4.x～7.x：编辑器骨架与四标签、草稿保留与离开提示、
基本信息标签（profile 提交 / 徽标与版本刷新 / 409 保留草稿 / 创建态）、模型与工具授权标签
（目录候选、`read+use` 与 `read+execute+configure`、独立提交、409 保留勾选）、租户管理标签
（稳定标识提交、未选中阻止、空间只读卡片、宿主路径不外泄、创建后停在基本信息）。

### 1.1 用例敏感性核验（防止「恒真」断言）

对新增断言做定点变异，确认每条断言都能失败（一次运行、运行后源码 md5 复原）：

| 变异 | 失败的用例 |
| --- | --- |
| 去掉「未选账号不得提交」的空值守卫 | `the admin tab refuses to submit while no account is picked` |
| 提交显示名而非稳定标识 | `the admin tab lists platform accounts and posts the picked stable id` |
| 在空间卡片中渲染 `shared_root` | `the admin tab renders the tenant space read-only and never a host path` |
| 创建成功后仍停留在 create 模式 | `after creation the editor becomes editable on the basics tab and points at the other tabs` |

四条变异各自只打中对应用例，说明断言不是恒真的。

## 2. 后端用例（`tests/test_identity_*.py` 与租户相关）

```
$ .venv/bin/python -m pytest tests/test_identity_web_handlers.py tests/test_identity_service.py \
      tests/test_identity_service_writes.py tests/test_identity_concurrency_acceptance.py \
      tests/test_identity_migration_drill.py tests/test_platform_user_admin.py \
      tests/test_tenant_create_containment.py tests/test_platform_admin_role_binding.py -q
146 passed in 12.16s
```

## 3. 端到端核对：真实 handler 走完整运营序列

脚本以真实 `web.application` + 临时 `identity.db` 驱动 handler（非浏览器），
按「新建 → 基本信息 → 模型授权 → 工具授权 → 指定管理员 → 停用 → 恢复」执行：

```
$ PYTHONPATH=. .venv/bin/python /tmp/e2e_tenant.py
1) create tenant
   http=200 OK id=tnt_... version=1 name=Beta
   PASS  create returns a tenant id at version 1
2) save basic info before any admin exists
   http=409 body={'status': 'error', 'message': 'tenant has no valid admin', 'code': 'no_admin'}
   PASS  enabling without a valid tenant admin is refused wholly  no_admin
   PASS  the refused save changed nothing
3) designate tenant admin
   http=200 OK {"status": "success", "membership": {"membership_id": "mem_..."}}
   PASS  admin binding succeeds
4) basic info: rename
   http=200 OK name=Beta Renamed active=True version=2
   PASS  rename bumps the version by exactly 1  1 -> 2
5) model grants
   http=200 OK grants=['model:use']
   PASS  model grant is stored
6) tool grants (the wholesale PUT must keep the model grant)
   http=200 OK kinds=['model', 'tool'] tool_actions=['configure', 'execute', 'read']
   PASS  model and tool grants coexist
   PASS  a tool keeps read/execute/configure
7) read the tenant space projection
   http=200 OK space={'id': 'beta', 'status': 'unavailable', 'isolation': 'dedicated-root'}
   (server-side shared_root is '/Users/jiantan/cow/tenants/beta', and must not appear in the response)
   PASS  space carries id/status/isolation
   PASS  no host path or shared_root in the response
8) disable
   http=200 OK active=False version=5
   PASS  disable persists active=false and bumps once
9) enable again
   http=200 OK active=True version=6
   PASS  enable persists active=true and bumps once
10) audit trail
   {"action": "tenant.create", ...}
   {"action": "tenant.set_admin", ...}
   {"action": "tenant.set_profile", ...}   # 第 4 步改名
   {"action": "tenant.resource_grants.set", ...}  # 模型授权
   {"action": "tenant.resource_grants.set", ...}  # 工具授权
   {"action": "tenant.set_profile", ...}   # 停用
   {"action": "tenant.set_profile", ...}   # 恢复
   PASS  tenant.create is audited
   PASS  tenant.set_admin is audited
   PASS  each accepted profile save writes exactly one tenant.set_profile  tenant.set_profile x3
   PASS  no audit row for the refused save
   per-tenant audit view (list_audit) shows: ['tenant.resource_grants.set']

16/16 checks passed
```

结论（已验收）：

- 「名称 + 启用状态」是一次提交、版本只递增 1、只写 1 条 `tenant.set_profile`；被拒绝的提交不写审计。
- 无有效 `tenant_admin` 时把租户置为启用会被整体拒绝（`no_admin`），且不产生部分变更。
- 工具授权与模型授权同库共存；按 kind 的整体 PUT 不会丢掉另一 kind 的既有授权。
- 平台侧读取返回只读空间投影（标识 / 状态 / 隔离类型），响应体不含 `shared_root` 或宿主绝对路径。

顺带记录的两点观察（非缺陷，供归档参考）：

1. `create_tenant` 响应里的 `version` 是展示值；编辑器在写入前会重新读取租户，因此实际使用服务端版本。创建后立刻用「创建响应里的版本」提交会得到 409 —— 前端已按重新读取实现，故不受影响。
2. `tenant.set_profile` / `tenant.set_admin` / `tenant.create` 的审计行沿用既有平台级生命周期写法（`tenant_id=NULL`，`target_tenant_id=<租户>`），因此 `list_audit(<租户>)` 只显示 `tenant.resource_grants.set`。这与既有 `tenant.rename` / `tenant.set_status` 完全一致，不是本次引入的偏差。
3. `openAdminModal` 的 `userpicker` 字段分支（`f.type === 'userpicker'`）仍保留为弹窗字段词汇表的通用能力，但移除 `openTenantAdmin` 之后已没有任何配置使用它；`admin_tenant_admin_edit` 文案键同样暂时无人引用。本次保留（编辑器直接复用 `_userPickerHtml` / `_initUserPicker`，未删除通用分支），若后续确认不再需要可按独立改动清理。

## 4. 未测试 / 未验收

- **浏览器视觉观感未验收**：已在真实浏览器中完成结构性与交互核对（见第 9 节），但未做观感评审（暗色模式、窄屏、标签禁用态与空间卡片的视觉细节）。
- **后端改动需重启进程才生效（已处理）**：:9899 原进程早于 `admin_handlers.py` 的修改，曾加载改动前的模块；已重启（新进程 11:49:40 启动）并用实机探针确认新模块生效。详见第 9.3 节。
- **真实库上的「成功保存」未执行**：`set_tenant_profile` 需要平台管理员近期密码，本次未取得该凭据，故未在运营库完成成功写入（临时库端到端 + 实机探针已覆盖；详见第 9.5 节）。
- **并发/多会话冲突未做真机演练**：409 路径由用例与 handler 校验覆盖，未做两个真实会话同时编辑同一租户的实机演练。
- **`tests/test_memory_global_config.py` 引起的用例串扰未修复**（详见第 5 节）：属既有问题，本 change 不扩大范围。
- 见 8.6：PRD-01～12 原文未恢复，标签划分与字段清单未与 PRD 逐条核对。

## 5. 全量回归与差异归因

```
$ .venv/bin/python -m pytest tests/ -q --ignore=tests/test_cli_e2e.py
33 failed, 1965 passed, 3 skipped
```

以 `git worktree` 检出 HEAD 基线（两次运行，结果完全一致，说明全量结果是确定性的）：

```
$ (HEAD 工作树) .venv/bin/python -m pytest tests/ -q --ignore=tests/test_cli_e2e.py
30 failed, 1932 passed, 3 skipped          # 两次运行失败集合完全相同
```

失败集合差异：

- 仅工作区多出 4 条：`test_consumer_closure_acceptance`（1）、`test_todo_service`（2）、`test_tenant_create_containment`（1）。
- 仅基线多出 1 条：`test_subagent.py::test_the_repo_ships_a_guide_that_documents_the_real_format`。

**归因（已核验，非本 change 引入）**：

1. 上述 4 条在工作区单独运行时全部通过：
   `pytest "tests/test_tenant_create_containment.py::...::test_create_without_base_rejected_when_workspace_is_default_root" tests/test_todo_service.py tests/test_consumer_closure_acceptance.py -q` → `16 passed`。
2. 最小复现：`pytest tests/test_memory_global_config.py tests/test_todo_service.py -q`
   - 工作区：`2 failed, 15 passed`
   - HEAD 基线：`17 passed`
   即 `tests/test_memory_global_config.py` 会遗留会话/鉴权全局状态，使随后的用例失败；这两个文件都不属于本 change。
3. 把本 change 的**两个源文件**（`auth/service.py`、`channel/web/admin_handlers.py`）临时回退到 HEAD 后，最小复现**仍然失败**（`2 failed, 15 passed`），随后已按 md5 校验复原。
   结论：触发源是工作区中其他在途改动的鉴权全局状态，与本 change 无关。

本 change 自身的定向回归（第 1、2 节）全绿。

## 6. 运行开关

本 change 未引入任何 feature flag，也没有 `staged` / `enforced` 门控：能力随代码发布直接生效（无新增环境变量读取）。
`git diff` 新增行中不含 `os.environ` / `getenv` / `flag` / `staged` / `enforced`（仅有注释文字命中 "flag" 一词）。

## 7. 现有数据库的一致性核对（8.4）

对工作区现有 `identity.db` 只读查询（`file:identity.db?mode=ro`）：

```
== 租户列表显示 vs tenants.active ==
code       name           active version  valid tenant_admin
default    默认租户                1       9                   1
test01     test01              1       1                   1
test02     test02租户            1       1                   0

== 历史不一致项（active=1 但无有效 tenant_admin）==
  test02 (test02租户): active=1, valid tenant_admin=0
```

「有效 tenant_admin」按 `_count_valid_tenant_admins` 的口径统计
（`memberships.active=1` 且 `users.active=1` 且 `roles.code='tenant_admin'`）。

**交运维确认的 1 项**：`test02` 处于启用状态但没有有效租户管理员。
本次不做任何自动数据修复。需要注意的运行时后果：由于本 change 之后「把租户置为启用」要求存在有效
`tenant_admin`，对 `test02` 的「基本信息」标签保存（在其仍为启用态时）会返回 409 `no_admin`，
需先在该租户的「租户管理」标签指定管理员，或先停用再保存。

审计侧同时可见：现有库中只有 `tenant.rename` / `tenant.create` / `tenant.resource_grants.set` /
`tenant.bootstrap`，尚无 `tenant.set_profile` —— 与本次新增的单事务合并写尚未在真实库中使用一致。

## 8. PRD 回填项（8.6）

PRD-01～12 原文尚未恢复，因此未能逐条核对租户控制台是否另有标签划分、字段清单或工具上限动作集要求。
本 change 按现有需求确认的结论落地：四个标签为「基本信息 / 模型授权 / 工具授权 / 租户管理」，
工具授权动作集为 `read` + `execute` + `configure`（`TENANT_GRANT_ACTIONS`），模型授权动作集为 `read` + `use`。
PRD 原文恢复后需要复核这三处口径是否一致。

## 9. 真实浏览器核对（:9899，已执行）

在真实运行的 Console 内（既有平台管理员会话，仅只读操作与标签切换，未点击任何保存按钮）
打开「租户管理 → 编辑」，核对结果如下。

### 9.1 前端结构核对（编辑 `test01`）

```
列表行按钮            ['创建租户', '编辑 tnt_ulzTIY4Y22oC4lJM', '编辑 tnt_q6G-P-5Iptgof342', '编辑 tnt_xNHlQIA2XP-z6nQG']
                      # 每行只剩「编辑」，原先独立的「管理」入口已按设计移除
标题 / 副标题          编辑租户 / test01
徽标 / 版本            启用 / 版本 1
标签                  basic=基本信息  model=模型授权  tool=工具授权  admin=租户管理
面板初始态             tenant-panel-basic=ACTIVE，其余 hidden
基本信息字段           code=test01  name=test01  active=true  recent_password=(空)
提交按钮              保存
```

四个标签的中文标签、徽标、版本与基本信息字段取值均与数据一致，创建态之外的标签初始只渲染
基本信息面板。

### 9.2 各标签实机加载

```
模型授权  3 项候选 = deepseek-v4-flash-vision-exp / deepseek-v4-flash / deepseek-v4-pro
          （带 provider:deepseek 前缀）+ 「清空 / 全选 / 已选 0 项」
工具授权  渲染为「未选择资源 / 清空 / 全选 / 已选 0 项」
租户管理  账号候选 3 个：系统管理员 admin · 平台管理员 / E2E普通成员 e2e_member / test01管理员 test01-admin
          （编辑器直接复用既有 userpicker）
```

工具授权为空是**数据侧真实为空**，不是前端缺陷 —— 直接请求目录接口核对：

```
GET /api/platform/tenants/tnt_ulzTIY4Y22oC4lJM/authorization/catalog?kind=model  -> 200 total=3
GET /api/platform/tenants/tnt_ulzTIY4Y22oC4lJM/authorization/catalog?kind=tool   -> 200 total=0
```

### 9.3 进程早于改动 → 重启 → 新模块生效（已解决）

**发现**：运行中的 :9899 进程启动于 10:47:29，而 `admin_handlers.py` 修改于 11:43:28（晚约 56 分钟）。
Python 在启动时导入模块，因此该进程仍在跑改动前的 handler，实机现象与之吻合：

```
$ lsof -nP -iTCP:9899 -sTCP:LISTEN
python3.1 22484 jiantan ... TCP *:9899 (LISTEN)
$ ps -o pid,lstart,command -p 22484
22484  Thu Sep 10 10:47:29 2026   /Users/jiantan/ai_assistant/cowagent/.venv/bin/python -u app.py
$ stat -f "%Sm %N" -t "%Y-%m-%d %H:%M:%S" channel/web/admin_handlers.py
2026-09-10 11:43:28 channel/web/admin_handlers.py

GET /api/platform/tenants/tnt_ulzTIY4Y22oC4lJM  -> 200，响应中 "space" in tenant == false
租户管理标签的空间卡片                            -> 「当前没有租户空间信息」
```

前端 JS 是从磁盘读取的静态资源，所以新版编辑器**已经**在真实浏览器里生效；只有后端 API 是旧的。

**先排除代码问题**：把**改动前的真实库副本**交给**当前代码**跑同一路由
（`PYTHONPATH=. .venv/bin/python /tmp/verify_space_live.py`，patch 掉平台管理员校验、只读）：

```
tenants in the live database: 4

test01         http=200 OK  space={"id": "test01",  "status": "unavailable", "isolation": "dedicated-root"}
test02         http=200 OK  space={"id": "test02",  "status": "unavailable", "isolation": "dedicated-root"}
test03         http=200 OK  space={"id": "test03",  "status": "unavailable", "isolation": "dedicated-root"}
default        http=200 OK  space={"id": "default", "status": "ready",       "isolation": "dedicated-root"}

OK: every tenant carries a space projection and no host path appears anywhere in the response.
```

空间投影在真实数据上按预期工作，且响应体不含 `shared_root` 与宿主绝对路径 —— 实机为空纯粹是进程未重启。

**重启与复验**：`kill 22484` 后端口在约 4 秒内由新进程接管（该部署由 launchd/supervisor 自动拉起，
无需手工 relaunch）：

```
$ ps -o pid,ppid,lstart,command -p 34591
34591     1 Thu Sep 10 11:49:40 2026   .venv/bin/python -u app.py
当前时间 11:49:54 —— 新进程启动于 handler 修改（11:43:28）之后，因此加载的是新模块。
```

重启后的实机复验（未使用密码、未写入任何数据）：

```
GET  /api/platform/tenants/tnt_ulzTIY4Y22oC4lJM
     -> 200，tenant.space = {"id":"test01","status":"unavailable","isolation":"dedicated-root"}
     租户管理标签空间卡片 -> 「空间标识 test01 / 状态 未就绪 / 隔离类型 dedicated-root」（无宿主路径）

POST 同路径 {"operation":"bogus"}
     -> 400 {"code":"invalid_request","message":"unknown operation"}

POST 同路径 {"operation":"profile","name":"test01","active":true,"expected_version":1}（无密码）
     -> 401 {"code":"invalid_old","message":"recent password required"}
```

两条 POST 探针是**新模块的判别器**：改动前的 handler 完全没有 `operation` 概念，
`{"operation":"bogus"}` 会退化成 `set_tenant_status` 并返回 401；只有新模块才会在进入服务层之前
以 400 `unknown operation` 拒绝。`profile` 探针返回 401 则证明请求确实路由到
`set_tenant_profile` 的 `_require_recent_password` 守卫（守卫之前无任何写入）。

探针后核对数据未被改动：

```
GET /api/platform/tenants/tnt_ulzTIY4Y22oC4lJM -> name=test01 active=1 version=1
```

### 9.4 真实数据上的空间状态

4 个租户里只有 `default` 的 `shared_root` 在磁盘上存在（`status=ready`），
`test01` / `test02` / `test03` 的根目录尚未创建（`status=unavailable`）。新编辑器会把
「未就绪」如实展示，这属于数据现状而非缺陷；可与第 7 节的 `test02` 缺管理员问题一并交运维处理。

### 9.5 仍未核对

- **真实库上的「成功写入」未执行**：`set_tenant_profile` 需要平台管理员本人的近期密码
  （`_require_recent_password` 无可绕过的分支），本次未取得该凭据，因此没有在运营库上完成一次成功保存。
  该路径已由临时库端到端（第 3 节）与上述两条实机探针覆盖；未覆盖的仅是「真实库 + 真实密码」的组合。
- 观感评审（暗色模式 / 窄屏 / 禁用态样式）与两个真实会话并发编辑同一租户的 409 演练仍未做。

### 9.4 真实数据上的空间状态

4 个租户里只有 `default` 的 `shared_root` 在磁盘上存在（`status=ready`），
`test01` / `test02` / `test03` 的根目录尚未创建（`status=unavailable`）。新编辑器会把
「未就绪」如实展示，这属于数据现状而非缺陷；可与第 7 节的 `test02` 缺管理员问题一并交运维处理。

### 9.5 仍未核对

- 保存写路径未在真实库上点击执行（只读核对 + 临时库 e2e 已覆盖），以免污染运营数据。
- 观感评审（暗色模式 / 窄屏 / 禁用态样式）与两个真实会话并发编辑同一租户的 409 演练仍未做。
