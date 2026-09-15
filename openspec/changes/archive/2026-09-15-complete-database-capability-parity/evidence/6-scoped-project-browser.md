# 6.1-6.6 个人项目浏览与受控本机导入（Web 接缝、导入边界与开放登记）

- 记录日期：2026-09-15
- 结论：项目浏览从整体关闭改为**本人项目根内**的受限浏览；本机目录导入由独立接缝实现，
  以上游 `_import_local_file` 声明的同一契约（loopback + 每启动令牌）为准并叠加数据库身份与一次性用途；
  发布原子、失败可补偿、配额先占，绝不静默覆盖已有项目。
- 合并说明：本轮先后有两份草稿（`6-project-browser.md` 记 HTTP/登记面、本文件记下层语义），
  已按下文合并为**一份**，原草稿删除；两份内容无冲突，唯一取舍是去掉重复的表格行。

> 说明：上游的 `_import_local_file` 在本树中**尚不存在**（其合并义务记于 `scripts/conflict-baseline.txt`，
> 由 `tests/test_upstream_core_seams.py::test_local_file_import_is_loopback_and_token_guarded_when_present`
> 在合入后强制其保留 loopback + 令牌校验）。本节所述契约由本 change 的
> `channel/web/project_import.py` 实现，并经真实 HTTP 用例验证；上游落地时两套校验必须同时成立，
> 不得以「已有导入路径」为由绕过其中一套。

## 0. 分工（任务 6.1-6.4）

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 项目根语义与原子复制 | `agent/workspace/project_browser.py` | 「项目根是什么」「如何原子复制一个目录」——不知道 HTTP、会话或句柄 |
| 本机导入边界（新增） | `channel/web/project_import.py` | 谁可以请求什么：loopback、每启动令牌、已验证的数据库身份（tenant + user）、把导入绑定到用户确认过的预览的一次性句柄、暂存前必须完成的配额预占、上传清单 |
| HTTP handler | `channel/web/web_channel.py` | 保持上游签名与响应形状的薄处理器，供控制台与 master 合并使用；拒绝一律带稳定 `code` |
| 开放登记与路由 | `auth/capability_matrix.py`（`project_browse` 切片）、`channel/web/route_registry.py` | 单一能力登记 + 单一路由表，`S(...)` 派生策略 |

没有第二套锚定路径工具（全部经 `common/safe_fs.py`）、没有第二套路由表、没有第二套项目存储。

服务端路径导入的四个条件（缺一即拒）：**loopback**（`REMOTE_ADDR` 为回环且无转发头，
反向代理不能把远端伪装成本机）、**每启动令牌**（本进程生成、以 `0600` 发布到
`local_import_token_file`，或由受管部署用 `local_import_token` 固定；常量时间比较；重启即失效）、
**数据库身份**（目标 tenant/owner 来自已验证会话，绝不来自请求体）、
**一次性句柄**（预览是用户确认的对象，导入必须出示同成员、同用途、同来源的句柄，首次使用即消费）。
浏览器无法看到服务器路径的**上传**传输不需要句柄：它不命名服务器路径，会话本身即授权。

允许的额外来源根来自配置 `project_import_source_roots`：请求可以在策略**之内**选择，但不能定义策略。

## 1. 浏览（任务 6.1、6.2）

- `ProjectBrowseHandler.GET` 以 `project_browser.normalize_selection` + `project_browser.browse` 解析入口，
  **保持旧 UI 依赖的字段名**：`status` / `path` / `parent` / `dirs`（`dirs` 条目仍是 `{name, path}`）。
  改变的是值的含义：**相对本人项目根的标识**，`""` 即根本身。
- **有意新增**两个字段并在 handler docstring 中说明：`breadcrumbs`（根到当前目录的同一套标识，
  可直接回传为 `path`，控制台因此永远不持有宿主路径）与 `scope`（`{kind: "personal", tenant_id, user_id}`，
  取自已验证请求上下文，**不接受参数**）。`parent` 在根处为 `None`，只能向上到本人根。
- `__DRIVES__` 哨兵在 handler 处即拒（`unsafe_path` / 403）：宿主驱动器列表不属于任何人的项目根。
- 历史绝对路径**仅当服务端证明其落在调用者本人根内**时才归一化为相对标识
  （`test_a_historical_absolute_path_inside_the_root_is_normalized`）；根外绝对路径拒绝
  （`test_an_absolute_path_outside_the_root_is_refused`）。
- 目录不存在返回 404（不是静默退回根），空根返回空列表：
  `test_a_missing_directory_is_a_404_not_the_root`、`test_a_fresh_member_without_a_projects_directory_gets_an_empty_root`。
- 只列本人当前 tenant+user 项目根内的目录：隐藏文件与软链接条目不提供。
- 遍历与宿主形状输入逐条拒绝：`..`、`alpha/../..`、`alpha/../../users`、`..%2F..%2Fetc`、
  `%2e%2e%2f%2e%2e`、`..\..\windows`、`C:\Windows`、`C:/Windows`、`\\server\share`、`//etc`、`/etc`、
  `alpha//inner`、`./alpha`、`__DRIVES__`（`test_traversal_and_host_shaped_identifiers_are_refused`，14 组）。
- 归属边界即权限边界：同租户其他成员看不到（`test_another_members_tree_is_invisible_to_a_peer`）；
  **租户管理员与平台管理员都不继承**成员私有树（`test_a_tenant_admin_does_not_inherit_a_members_private_tree`、
  `test_a_platform_admin_does_not_inherit_a_members_private_tree`）；跨租户历史路径与跨租户成员都被拒
  （`test_a_cross_tenant_historical_path_is_refused`、`test_a_cross_tenant_member_cannot_reach_this_tenants_root`）。
  这里没有 `agent.read` / 平台 `all` / 字符串前缀捷径：复用的是既有 owner/resource 授权 helper。
- 平台根中嵌套的成员私有目录不会被外层根暴露
  （`test_private_directories_nested_in_an_outer_root_are_never_exposed`）。
- 每次请求都由 `common/safe_fs.py` 针对已验证身份重解析：软链接与「检查后替换」都被拒绝。

## 2. 选择（任务 6.2）

`ProjectSelectHandler` 在写入前重解析真实路径并重验会话归属，六种情况分别拒绝：
目录被换成软链接、属于其他成员、标识越界、目录已消失、会话属于其他成员、会话不是 Web 会话
（`test_select_refuses_*` 六例）；根内旧绝对路径同样归一化
（`test_select_normalizes_a_legacy_absolute_path_inside_the_root`）。
因此「浏览之后身份或路径改变」不会让写入落到别处。

## 3. 导入（任务 6.3、6.4）

| 要求 | 实现与证据 |
| --- | --- |
| 本机来源必须 loopback | `test_a_local_source_path_is_refused_from_a_remote_peer`、`test_a_forwarded_local_path_is_refused` |
| 必须带每启动令牌 | `test_a_local_source_path_requires_the_per_start_token` |
| 预览报告目标/规模/冲突，已有目标不覆盖 | `test_preview_reports_the_target_scale_and_conflict`、`test_preview_reports_an_existing_target_instead_of_overwriting` |
| 来源必须在允许根内 | `test_preview_refuses_a_source_outside_the_allowed_roots` |
| 平台数据目录（含运行记录与身份库）不可导入 | `test_preview_refuses_a_source_inside_the_platform_data_directory` |
| 预览可绑定到本人会话 | `test_preview_can_be_bound_to_an_owned_session` |
| 无句柄的浏览器路径被拒 | `test_import_refuses_a_browser_supplied_path_without_a_handle` |
| 未知/他人句柄被拒 | `test_import_refuses_an_unknown_or_foreign_handle` |
| 原子发布 + 句柄一次性 | `test_import_publishes_atomically_and_the_handle_is_single_use`、`test_import_refuses_a_resubmitted_source_under_a_used_handle` |
| 绝不覆盖已有项目 | `test_import_never_overwrites_an_existing_project`、`test_an_upload_never_overwrites_an_existing_project` |
| 来源与预览不一致被拒 | `test_import_refuses_a_source_that_differs_from_the_preview` |
| 排除软链接条目 | `test_import_excludes_symlinked_entries` |
| 配额**在暂存之前**预占；并发争用只剩一格时至多一个成功 | `test_import_quota_reservation_refuses_before_staging`、`test_two_imports_racing_for_the_last_slot_resolve_to_one_winner` |
| 取消不发布 | `test_a_cancelled_handle_publishes_nothing` |
| 中途取消回滚暂存树 | `test_a_mid_flight_cancel_rolls_the_staging_tree_back` |
| 发布失败补偿并释放配额 | `test_a_failed_publish_compensates_and_releases_the_slot` |
| 远端 peer 与客户端自定义选项被拒 | `test_import_refuses_a_remote_peer_and_a_client_supplied_option` |
| 远端后端用受保护上传，不解释服务器路径 | `test_an_upload_manifest_is_validated_and_traversal_is_refused`、`test_an_upload_publishes_without_interpreting_a_server_path`、`test_an_upload_still_requires_identity_and_origin` |

- 目录遍历与导入判定位于 `agent/workspace/project_browser.py`（根语义/原子复制）与
  `channel/web/project_import.py`（边界/令牌/句柄/配额/上传清单）两个模块，handler 只是薄壳；
  未改动任何单文件上传入口的签名。
- 上传传输不需要句柄（它不命名服务器路径，会话本身即授权），但清单条目同样逐个经 `safe_fs` 校验，
  遍历条目在写入前被拒；`upload_params()` 直接使用 `multipart.parse_form_data`，
  因为 web.py 的 `rawinput()/dictify` 会把同名多文件折叠成最后一个，导致目录上传丢文件。

## 4. 开放（任务 6.6）

`auth/capability_matrix.py` 的 `project_browse` 切片（`capability="scoped-project-browser"`）：
`open={"browse": ACCESS_READ, "import": ACCESS_EXECUTE}`、`implemented=True`、`accepted=True`、`reason=""`、
策略 `tenant`；注释点名验收测试 `tests/test_scoped_project_browse.py`，并如实写出 upload 分支
**未**验证的部分（Desktop 原生选择器）。

`channel/web/route_registry.py` 经 `S("project_browse", ...)` 派生：`/api/projects/browse` 的 `GET` 使用
`S("project_browse", "browse")`；新增 `/api/projects/import/preview`、`/api/projects/import`、
`/api/projects/import/cancel` 三个 `POST`，均使用 `S("project_browse", "import")`，来源标记
`fork:project-import`。没有第二张路由表。该路由上原有的 `_guard_not_database` 整体关闭已移除，
无「声明开放、实际仍拒」的残留路径。

`scripts/route-baseline.txt` 的 evolution 段记录 `browse` 由 `closed` 改为 `tenant` 并新增三行 import 方法
（追加历史、不删旧行）。页面临界投影：切片 `page=None`（入口在工作台内，不是独立菜单页），因此不新增页面；
`tests/test_capability_matrix.py`、`tests/test_recovered_entry_acceptance.py` 与
`tests/test_route_registry.py` 锁定「开放登记、路由与闸门」三者一致。

## 5. 验证证据

```
.venv/bin/python -m pytest tests/test_scoped_project_browse.py -q -p no:randomly
→ 62 passed in 49.21s

.venv/bin/python -m pytest tests/test_scoped_project_browse.py tests/test_project_browser.py \
    tests/test_private_agent_file_scope.py tests/test_platform_file_browsing.py -q -p no:randomly
→ 142 passed, 19 subtests passed

.venv/bin/python -m pytest tests/test_scoped_project_browse.py tests/test_project_browser.py \
    tests/test_platform_file_browsing.py tests/test_console_workspace_transport.py \
    tests/test_http_policy.py tests/test_route_registry.py tests/test_capability_matrix.py \
    tests/test_recovered_entry_acceptance.py tests/test_upstream_core_seams.py -q -p no:randomly
→ 228 passed, 1 skipped, 19 subtests passed
```

- `tests/test_scoped_project_browse.py`：62 例，覆盖浏览、选择、本机导入、取消/补偿、配额与上传清单（上表逐条对应）。
- `tests/test_project_browser.py`：52 例，覆盖项目根解析、原子复制、并发与冲突、平台根嵌套等下层语义。
- `tests/test_private_agent_file_scope.py`、`tests/test_platform_file_browsing.py`：既有文件浏览回归，
  证明私有 Agent 与平台文件面的作用域未被本次改动放宽（未通过删测试放行）。
- 那 1 skipped 是 `tests/test_upstream_core_seams.py::...test_local_file_import_is_loopback_and_token_guarded_when_present`：
  上游 `_import_local_file` **尚未落入本树**，该用例在函数出现前跳过，出现后强制其保留 loopback + 令牌校验。
- `tests/test_http_policy.py` 中「仍在关闭的消费者返回 503」用例改为显式补丁 `ROUTE_POLICY` 后验证，
  不再依赖「恰好有某个自然关闭的路由」；`projects/browse` 的用例已改为断言其**开放**（缺租户 400、未认证 401）。
- 未删除或放宽任何既有断言；未运行整套测试；未运行浏览器/Electron 自动化。

## 6. 未覆盖与边界

- **Desktop 原生选择器**：端到端需要真实 Desktop 客户端（原生目录选择 → loopback + 令牌 → 预览 → 发布）。
  本轮验证的是同一接缝上的真实 HTTP 行为（loopback、令牌、身份、句柄、上传两条传输），
  真实客户端演练属于 Desktop 侧任务 8.7，本文件不对其作任何「已验证」声明。
- **上游 `_import_local_file`**：本树尚无该函数，因此「保留其接口」目前体现为
  (a) 同形状契约在本 change 的接缝实现、(b) 合并义务记录于 `scripts/conflict-baseline.txt`、
  (c) 上游落地后由 `test_upstream_core_seams.py` 强制两套校验并存。此处不声称「已验证上游函数未被改动」。
- **真实远端部署**：上传传输经真实 `build_web_app()` 应用以 multipart 请求验证（远端 peer 由 `REMOTE_ADDR` 模拟），
  但未在真实反向代理/远端主机拓扑上演练。
- **配额并发**：并发用例覆盖两个导入争用最后一格（至多一个成功）；跨进程配额一致性不在本轮范围。
- 允许的额外来源根是部署策略，不是请求参数；把来源根做成可请求的内容会被拒绝。
