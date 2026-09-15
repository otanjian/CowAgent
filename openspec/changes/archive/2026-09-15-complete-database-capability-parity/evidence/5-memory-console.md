# 5 记忆浏览与个人记忆衔接（5.1-5.4 证据，5.5 缺口）

- 记录日期：2026-09-15
- 方法：TDD——先写 `tests/test_memory_console_scope.py`（走真实 `build_web_app()`），
  再实现 `channel/web/memory_console.py`，最后重构两个 handler 并开放切片。所有命令均用
  `.venv/bin/python -m pytest <files> -q -p no:randomly`。
- 结论概要：`GET /api/memory` 与 `GET /api/memory/content` 已作为「作用域显式化的兼容读入口」
  开放（切片 `memory_browse`，策略 `tenant` + `memory.read`，只开门禁放行、仍受 HTTP gate 约束）；
  本人个人记忆、私有 Agent 归属、共享 Agent、跨租户与非 owner 拒绝、未知 scope/category/
  条目拒绝、软链接拒绝均由同一共享服务承担。5.5（个人记忆 CRUD 联合验收）**未完成**，原因见第 5 节。

## 1. 任务 5.1：显式目标解析与兼容字段（已实现）

新增 fork 侧接缝 `channel/web/memory_console.py`（单一职责：目标解析 + 委派；不含第二套 CRUD、
第二套索引或第二张路由表）。

| 作用域 | 目标 | 归属判定 | 读取实现 |
| --- | --- | --- | --- |
| `personal` | 当前有效 tenant/user 的个人记忆域 | 由 `_db_scope()` 的 `RequestContext` 固定，参数无法指定他人 | 已交付 `agent/memory/personal.py::PersonalMemoryService` |
| `private_agent` | 当前租户的私有 Agent 记忆 | `_require_tenant_agent_binding` + `_require_private_owner`，**在读取之前**执行；租户管理员非 owner 同样拒绝 | `agent/memory/service.py::MemoryService` |
| `shared` | 当前租户的共享 Agent 记忆 | 同上绑定校验，无 owner 时按现有共享授权 | 同上 |

- 作用域词表 `personal` / `private_agent` / `shared` 显式声明（`memory_console.SCOPES`）。
- 拒绝码（稳定机器码，随 `{"status":"error","code":...,"message":...}` 返回）：
  `unknown_scope` `ambiguous_target` `unknown_category` `unknown_agent` `unknown_entry`
  `entry_required` `invalid_entry` `invalid_paging` `not_owner` `unsafe_path` `memory_unavailable`。
- 无共享根默认回退：缺 `scope` 且缺 `agent_id` 时解析为**本人个人域**（不是租户共享根，也不是
  另一个人的目录）；`scope=personal` 与 `agent_id` 同时出现按 `ambiguous_target` 拒绝；
  未知 `category` 直接拒绝而不是落到共享根；跨租户标识沿用既有非披露答案（`unknown_agent`/404）。
- 兼容字段保留：列表 `page` / `page_size` / `total` / `list[]`（`filename` `type` `size`
  `updated_at`），正文 `filename` / `rel_path` / `content`；个人域行额外给出 `id` / `revision`
  与 `read_only: true`（该入口不提供写动作，写仍走 `POST /api/memory/personal`）。
- 旧的显式智能体形状（`agent_id=...`，无 `scope`）照旧返回兼容字段，并标注真实作用域
  （`scope` 字段），不静默替换为默认智能体或共享根。

## 2. 任务 5.2：同一归属判断（部分由兄弟接缝提供，如实说明）

- 本入口的所有权判定只调用 `channel/web/web_channel.py` 既有的
  `_require_tenant_agent_binding` / `_require_private_owner`（同一实现，不自建第二套规则），
  个人域只经 `_personal_memory_service(ctx)`（与 `/api/memory/personal*` 同一个服务实例构造）。
- 确认同一共享服务承担边界：`tests/test_memory_console_scope.py::test_both_personal_entries_refuse_the_same_symlinked_entry`
  用同一个越界软链接同时打兼容入口和已开放 `/api/memory/personal*`，断言两者都是 403 且
  `code` 相同（证明边界在共享服务，而不是只修了新入口）。
- 平台文件根 / 工作区别名 / 管理员旁路（文件预览、`/api/workspace*`、平台文件浏览）属兄弟接缝
  范围，本次未逐条复验，只跑了既有 `tests/test_platform_file_browsing.py`（通过）。

## 3. 任务 5.3：真实身份库 + 文件链路验证（已实现）

`tests/test_memory_console_scope.py`（新增，28 个用例，全部经 `build_web_app()` 的真实 WSGI 应用）：

| 矩阵 | 用例 |
| --- | --- |
| 本人（正向） | `test_a_member_lists_and_reads_their_own_personal_memory`、`test_the_legacy_shape_without_a_scope_reads_my_own_personal_memory`、`test_pagination_is_honoured` |
| 旧客户端指定智能体 | `test_a_missing_scope_with_an_agent_reads_that_agents_memory`、`test_a_known_category_is_still_served_for_an_agent` |
| 私有 Agent 本人 | `test_the_owner_reads_their_private_agents_memory` |
| 同租户他人 / 租户管理员非 owner | `test_a_non_owner_is_refused_before_any_read`（404/403，**读取前**拒绝：记忆服务被替换为 tripwire）、`test_the_legacy_agent_id_shape_keeps_the_same_owner_gate` |
| 跨租户 / 同账号跨租户 | `test_a_member_of_another_tenant_is_refused`、`test_another_tenants_agent_is_not_addressable`、`test_the_same_account_bound_to_another_tenant_reads_only_its_own` |
| 未知 scope / category / 条目 | `test_an_unknown_scope_is_refused`、`test_an_unknown_category_is_refused`、`test_the_personal_scope_refuses_an_agent_target`、`test_a_content_request_without_an_entry_is_refused`、`test_an_unknown_entry_is_refused`、`test_a_malformed_personal_entry_id_is_refused` |
| 门禁仍生效 | `test_the_gate_still_refuses_an_anonymous_caller`（401） |
| 软链接 | `test_an_entry_symlink_out_of_the_root_is_refused`（403 `unsafe_path`）、`test_an_intermediate_directory_symlink_is_refused`、`test_a_symlinked_personal_root_is_refused`、`test_a_symlinked_agent_memory_directory_is_not_followed`、`test_a_symlinked_agent_entry_is_not_served`、`test_both_personal_entries_refuse_the_same_symlinked_entry` |
| 已开放个人入口回归 | `test_the_personal_endpoints_still_behave`、`test_the_compat_personal_scope_does_not_expose_another_member` |
| 注册表与路由一致 | `test_the_capability_registry_and_the_route_table_agree`（切片 `open` 与派生路由逐项一致） |

软链接守卫是否「承重」的探针（把两个守卫换成放行版本后重跑 `-k symlink`）：

```
$ .venv/bin/python -c "import sys, os; sys.path.insert(0, os.getcwd()); \
    import common.safe_fs as sf; sf.resolve_within = lambda r, x: None; sf.is_symlink = lambda r, x: False; \
    import pytest; sys.exit(pytest.main(['tests/test_memory_console_scope.py','-q','-p','no:randomly','-k','symlink']))"
2 failed, 4 passed, 22 deselected in 10.05s
FAILED tests/test_memory_console_scope.py::test_a_symlinked_agent_memory_directory_is_not_followed
FAILED tests/test_memory_console_scope.py::test_a_symlinked_agent_entry_is_not_served
```

结论（如实）：agent 作用域的两个软链接用例在去掉 `memory_console` 的守卫后**确实失败**，
说明这两个守卫承重；另外 4 个个人域用例由共享服务 `agent/memory/personal.py` 经
`safe_fs.is_file` / `list_names` / `read_text` / `stat`（打开时校验接缝）拒绝，探针只关闭了
`resolve_within` / `is_symlink` 两个谓词，没有关闭该打开时接缝，因此它们照旧通过——这也说明
个人域的边界确实在原共享服务里（5.6 的范围），而不是被本入口「顺带保护」。

未覆盖（缺口）：**「检查后替换」的受控竞态**没有在本次加入。本入口每次请求都重新解析、并把
「路径是不是根内普通文件」与实际读取交给共享服务的打开时校验，但真正关闭校验与打开之间的窗口
属 5.6/5.8（受控并发屏障）交付范围，本次未做交错验证。

## 4. 任务 5.4：开放 2 个读方法（已实现）

- `auth/capability_matrix.py` 切片 `memory_browse`（capability `database-memory-console`，
  page `admin.memory`）：`open={"list": ACCESS_READ, "content": ACCESS_READ}`、
  `implemented=True`、`accepted=True`、`reason=""`；`scope` / `policy` / `permission` 保持原值，
  注释写明验收由 `tests/test_memory_console_scope.py` 背书（限长注释，不用 `reason`）。
- `channel/web/route_registry.py`：两条既有条目改用同一 `S(...)` 助手
  （`S("memory_browse", "list")` / `S("memory_browse", "content")`），未新增路由表。
  派生结果：

```
$ .venv/bin/python -c "from channel.web.route_registry import derive_route_policy; d=derive_route_policy(); print(d['/api/memory'], d['/api/memory/content'])"
{'GET': {'policy': 'tenant', 'comment': 'memory_browse: list (read)', 'permission': 'memory.read'}}
{'GET': {'policy': 'tenant', 'comment': 'memory_browse: content (read)', 'permission': 'memory.read'}}
```

与 `scripts/route-baseline.txt` 的可追溯行（288/289：`tenant  memory.read`）一致；两个方法仍
只经 HTTP gate 放行（匿名 401、无租户选择 400、无权限 403、有权限到达 handler）。

- handler 重构：`MemoryHandler.GET` / `MemoryContentHandler.GET` 委派给 `memory_console`，
  拒绝以 `web.HTTPError(status, ...)` + JSON 体返回（400 参数、403 非本人/跨租户、
  404 未知条目、503 身份或记忆存储不可用），不再保留「一律 200 + `{"status":"error"}`」形状；
  只有已授权且存储可用时才返回 200。

## 5. 任务 5.5：未完成，以及未满足的要求（如实记录）

- **5.5 未完成**：联合验收要求「编辑/删除/清空、索引失败恢复、跨获准 Agent 一致性、旧任务防回写」，
  且必须使用 5.6-5.8 修复后的实现版本。本 change 的 `evidence/` 目录当前没有 5.6-5.8 的证据文件，
  即正文/索引/清空的操作版本与 generation 协调、重启恢复与受控并发交错尚未交付到位；
  因此 5.4 在 `tasks.md` 中声明的前置「5.3、5.6-5.8 通过」只满足 5.3。本次按协调者指令开放的是
  **两个只读方法**，其拒绝语义不依赖发布协议；但「浏览与个人写入同一内容版本」的断言（规格中
  「记忆浏览与个人写入使用同一内容版本」）在本文件内**没有**被验证，结论只能到「读入口可用且
  归属正确」为止，不能声称 5.5 通过。
- **检查后替换竞态**：见第 3 节末，未做受控交错验证。
- **页面侧交接（9.1）**：个人域响应带 `read_only: true`、`actions.edit/delete = false`，
  `rel_path` 是**个人根相对条目 id**（不是工作区路径，也不是 `/api/memory/personal` 之外的写地址）。
  `admin.memory` 页若沿用工作区文档写接口，会把个人条目 id 当工作区路径写进租户共享记忆目录；
  页面接入时必须把 `scope=personal` 的行改走 `POST /api/memory/personal`（或只读展示）。
- **本入口之外的既有失败（非本接缝引入）**。这些断言的对象都是**别的切片**，且随其它 agent
  并发开放而变化；`/api/memory*` 自身在下列文件里全部通过：
  - `tests/test_recovered_entry_acceptance.py` 5 个失败全部指向 `/api/weixin/qrlogin`
    （`weixin_scan` 切片）；`/api/memory*` 在该文件的四项门禁（匿名 401、无租户 400、
    有权限到达 handler、无权限 403）全部通过。
  - `tests/test_http_policy.py` 4 个失败：`test_projects_browse_still_closed_in_database`（`/api/projects/browse`
    已是 `tenant`）、`test_still_closed_interactive_channel_actions`（`/api/weixin/qrlogin` 已不再 503），
    以及 `test_closed_consumer_503_in_database` / `test_admin_cannot_override_still_closed_consumer`
    这两个**动态查找**用例——它们要求「还存在至少一个 `closed` 路由」，而当前
    `derive_route_policy()` 已无任何 `closed` 路由（其它切片也已开放），失败信息为
    `no closed route left; this test no longer proves anything`。这是整 change 的开放进度问题，
    不是本接缝引入；此刻 `derive_route_policy()` 中 `/api/memory` 与 `/api/memory/content`
    均为 `{'policy': 'tenant', 'permission': 'memory.read'}`。
  - `tests/test_route_registry.py::test_derived_policy_matches_frozen_baseline` 曾在
    `/api/projects/browse`（基线第 69 行仍为 `closed`）上失败，属 9.2/6.x 的基线登记工作，
    本文件不动 `scripts/route-baseline.txt`；最近一次运行该文件已通过。
- **一处既有单测的改写**：`tests/test_doc_edit.py::test_memory_content_handler_includes_the_editable_path`
  原来断言「无 scope、无 agent_id 的 `/api/memory/content` 读取工作区根 MEMORY.md」，
  这正是 5.1 明确禁止的共享根默认回退，已被本次改动作废。该用例改为显式指定 Agent 目标
  （`agent_id=primary`，绑定桩返回未私有），保留其原意「正文响应给出编辑可写回的 workspace 相对路径」
  并补断 `scope == "shared"`。

### 5.1 与 `evidence/5-memory-browse.md` 的关系（本节写完后补记）

上面第 5 节写「`evidence/` 没有 5.6-5.8 的证据文件」，是本文件写作时的状态，**已被随后的文件取代**：
并行完成的服务侧补强证据在 `evidence/5-memory-browse.md`，它记录了

- 5.6：`agent/memory/personal.py` 六个动作全部经 `common/safe_fs.py` 锚定访问（逐段 `O_NOFOLLOW`，
  使用校验过的目录描述符），软链接与「检查后替换」都被拒绝；
- 5.7/5.8：`.memory-scope.json` 的 `op_version`/`generation` 协调、发布意图持久化与
  `recover_incomplete_publish` 重启恢复、索引发布的版本复核；
- 5.5：新兼容入口与 `/api/memory/personal*` 共用同一服务，编辑/删除/清空的版本条件与
  旧任务防回写由同一套版本协议覆盖。

因此本文件第 5 节的结论收窄为：**本接缝自身的证据到「读入口可用且归属正确」为止**；
5.5 的结论以 `5-memory-browse.md` 为准（该文件列出的命令为 115 passed）。两处对同一代码的判断不冲突，
只是记录时点不同。第 3 节末「检查后替换的受控竞态未做交错验证」的边界仍然成立
（`5-memory-browse.md` 覆盖的是打开时校验与版本条件，不是交错压力测试）。

## 6. 命令与结果（原始输出摘录）

```
$ .venv/bin/python -m pytest tests/test_memory_console_scope.py -q -p no:randomly
28 passed in 36.53s

$ .venv/bin/python -m pytest tests/test_memory_console_scope.py tests/test_personal_memory_console.py \
      tests/test_personal_memory_tool_execution.py tests/test_platform_file_browsing.py \
      tests/test_doc_edit.py -q -p no:randomly
111 passed in 48.32s

$ .venv/bin/python -m pytest tests/test_memory_console_scope.py tests/test_personal_memory_console.py \
      tests/test_platform_file_browsing.py tests/test_personal_memory_tool_execution.py \
      tests/test_http_policy.py tests/test_route_registry.py -q -p no:randomly
4 failed, 139 passed in 52.37s      # 失败全部落在别的切片（见第 5 节）：
                                    # /api/projects/browse ×1、/api/weixin/qrlogin ×1、
                                    # 「已无 closed 路由」的动态用例 ×2；
                                    # 此前同一集合为 2 failed, 140 passed（其它切片开放前）

$ .venv/bin/python -m pytest tests/test_doc_edit.py -q -p no:randomly
26 passed in 0.30s

$ .venv/bin/python -m pytest tests/test_doc_edit.py tests/test_recovered_entry_acceptance.py \
      tests/test_tenant_read_scoping.py tests/test_personal_console_multi_tenant_authorization.py \
      tests/test_capability_matrix.py -q -p no:randomly
5 failed, 71 passed in 22.35s       # 5 个失败均为 /api/weixin/qrlogin

$ .venv/bin/python -c "from channel.web.route_registry import derive_route_policy as d; p=d(); \
      print([(k, m) for k, ms in p.items() for m, e in ms.items() if e['policy']=='closed'])"
[]                                  # 已无 closed 路由：动态「仍关闭」用例的前提消失（非本接缝）
```

## 7. 交付物

| 文件 | 变更 |
| --- | --- |
| `channel/web/memory_console.py` | 新增：作用域解析 + 拒绝码 + 委派（无第二套 CRUD/索引/路由表） |
| `channel/web/web_channel.py` | `MemoryHandler.GET` / `MemoryContentHandler.GET` 改为委派并把拒绝变成 `web.HTTPError` + JSON |
| `auth/capability_matrix.py` | 切片 `memory_browse` 开放 list/content 两个读方法并标记验收 |
| `channel/web/route_registry.py` | 两条条目改用 `S("memory_browse", ...)` |
| `tests/test_memory_console_scope.py` | 新增 28 个用例（真实 `build_web_app()`） |
| `tests/test_doc_edit.py` | 1 个既有用例改指显式 Agent 目标（原断言为本次作废的共享根回退） |
