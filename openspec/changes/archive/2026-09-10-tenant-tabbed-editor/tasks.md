## 1. 前置：确认在途依赖与基线证据

- [x] 1.1 确认 `simplify-tenant-creation-form` 与 `tenant-admin-account-picker` 的落地状态（`openTenantCreate` 字段是否已收敛为 `code`/`name`/`recent_password`；`userpicker` 是否已实现），并记录与本 change 的接续冲突点
      - 两个 change 已在工作区落地（未提交）：`openTenantCreate` 字段已收敛为 `code`/`name`/`recent_password`；`userpicker` 已实现（`_initUserPicker`，`openTenantAdmin` 已使用）。
      - 接续冲突点 1：`tests/test_tenant_create_frontend.cjs` 的 `create success chains into the tenant admin dialog` 断言创建成功后仍打开管理员对话框。
      - 接续冲突点 2：同文件 `tenant rows expose no cross-tenant role button` 断言行内存在 `'tenant','admin'`。
- [x] 1.2 定位并记录 `tests/test_tenant_create_frontend.cjs` 中断言「租户行内存在 `edit` 与 `admin` 两个按钮」的确切位置，作为后续必须同步更新的证据
      - `tests/test_tenant_create_frontend.cjs:231-242`，第 241 行 `assert.ok(html.includes("'tenant','admin'"), 'admin row action stays')` 为待更新断言。
- [x] 1.3 定位并记录 `tests/test_identity_web_handlers.py` 中覆盖 `PlatformTenantHandler.POST` 改名与停用的现有用例，确认旧派发回退必须继续通过
      - `test_tenant_rename_via_handler`（530-539）与 `test_tenant_rename_version_conflict`（541-548）均只发送 `name`，无 `operation`，走旧派发；两者必须在改动后继续通过。
- [x] 1.4 运行相关既有测试取得基线并保存输出：`tests/test_tenant_create_frontend.cjs`、`tests/test_tenant_admin_account_picker.cjs`、`tests/test_identity_web_handlers.py`、`tests/test_identity_service.py`、`tests/test_identity_service_writes.py`
      - `node --test tests/test_tenant_create_frontend.cjs tests/test_tenant_admin_account_picker.cjs` → 15 pass / 0 fail。
      - `.venv/bin/python -m pytest tests/test_identity_web_handlers.py tests/test_identity_service.py tests/test_identity_service_writes.py -q` → 83 passed。
- [x] 1.5 记录「启用状态从未保存」的现状证据（一次真实的改名+切换启用提交后 `tenants.active` 未变化），作为修复后可对照的前后证据
      - 走真实 handler 派发提交 `{name:"Acme Renamed", active:false, expected_version:1, recent_password:...}`：
        `BEFORE name='Acme' active=1 version=1` → `HTTP 200` → `AFTER name='Acme Renamed' active=1 version=2`，
        `active changed: False`，审计仅 `tenant.rename`（无 `tenant.set_status`）。缺陷复现确认。
- [x] 1.6 确认归档顺序前提：`tenant-admin-account-picker` 先归档、`tenant-tabbed-editor` 后归档；核对本 change 的 spec delta 对「租户写入使用版本与同库审计」的改动确实包含该 change 已写入的增量，避免归档时丢内容
      - 确认重载：`tenant-admin-account-picker` 修改「平台直接配置管理员且不接管账号」与「租户写入使用版本与同库审计」两条；本 delta 与后者重叠并已并入其增量（「候选账号加载失败」场景 + 读取语义句）。
      - `simplify-tenant-creation-form` 修改「创建租户一次建立可用身份关系」，与本 delta 无重叠。

## 2. 后端：租户名称与启用状态的单事务合并写

- [x] 2.1 先写失败用例：同时改名与启用成功，租户版本只递增 1，且只写入一条 `tenant.set_profile` 审计
      - `tests/test_identity_service_writes.py::TenantProfileTests::test_rename_and_disable_commits_once_with_single_audit`。RED 于 `AttributeError: 'IdentityService' object has no attribute 'set_tenant_profile'`；GREEN 后通过。
- [x] 2.2 先写失败用例：同时改名并将 active 置为 true 但该租户无有效 `tenant_admin` 时整体拒绝，名称与 active 均不变
      - `test_rename_and_enable_without_valid_admin_is_rejected_wholly`，断言 `code="no_admin"`、`status=409` 且名称回滚为 `Beta`。
- [x] 2.3 先写失败用例：同时改名并停用时若会破坏活动成员租户连续性，整体拒绝且不产生部分变更
      - `test_rename_and_disable_breaking_continuity_is_rejected_wholly`，断言 `code="last_active_tenant_required"`，名称/active/version 三者均未变。
- [x] 2.4 先写失败用例：过期 `expected_version` 返回 409 且无部分变更；审计写入失败返回 503 且全部回滚
      - `test_profile_version_conflict_changes_nothing`（409 + 无变更）与 `test_profile_rolls_back_when_audit_write_fails`（patch `_audit_in_tx` 抛错 → 全部回滚，无 `tenant.set_profile` 审计）。
      - 口径说明：审计失败的**回滚**已验收；「返回 503」是既有基线规范中继承的措辞，本路径与 `set_tenant_name`/`set_tenant_status` 一致，审计异常经 `_service_error` 归为 500 `internal`，**未实现 503 映射**（既有缺口，本 change 未扩大范围），在 `evidence.md` 中如实标注。
- [x] 2.5 先写失败用例：非平台管理员、近期密码不通过、`name` 为空三条拒绝路径
      - `test_profile_rejected_paths_change_nothing`，三条断言 403 / `invalid_old` / `bad_request` 且租户行未变。
- [x] 2.6 实现 `IdentityService.set_tenant_profile`，复用既有守卫辅助方法，不复制守卫逻辑
      - `auth/service.py` 紧随 `set_tenant_name` 之后；复用 `_count_valid_tenant_admins` 与 `_check_all_affected_active_tenant_continuity`，单条 `UPDATE ... version=version+1` 与单条审计。
- [x] 2.7 为 `PlatformTenantHandler.POST` 增加显式 `operation` 字段（`profile` / `name` / `status`），并保留无 `operation` 时的 `name` 键旧派发
      - `channel/web/admin_handlers.py`；未知 `operation` 返回 400 `invalid_request`，不静默落到写路径。TDD 顺序调整为「先 2.8 用例、后 2.7 实现」。
- [x] 2.8 补 handler 层用例：`operation:"profile"` 同时改名与启用成功；无 `operation` 的旧请求行为与改动前一致
      - 新增 5 条于 `tests/test_identity_web_handlers.py`：profile 同时改名+停用、profile 版本冲突、profile 启用缺管理员被拒、`operation:"status"` 只停用不改名、未知 operation 400。
      - 旧派发兼容由既有 `test_tenant_rename_via_handler` / `test_tenant_rename_version_conflict` 继续通过覆盖。
      - handler 级前后证据：`operation:"profile"` 提交后 `active changed: True`、`version delta: 1`、`tenant.set_profile` 审计恰好 1 条。

## 3. 后端：租户空间只读投影

- [x] 3.1 先写失败用例：平台侧单个租户读取返回空间标识、可用状态与隔离类型，且响应体不含 `shared_root` 或任何宿主绝对路径
      - `test_platform_tenant_read_exposes_read_only_space` 与 `test_tenant_space_reports_ready_for_an_existing_root`。RED 于 `KeyError: 'space'`；GREEN 后通过，并断言响应原文不含 `shared_root`、不含 `/s/acme` 与实际根目录。
- [x] 3.2 先写失败用例：成员只读路径 `GET /api/tenant` 的响应不因本次改动扩大可读字段
      - `test_member_tenant_read_has_no_space_or_host_path`。该用例为「断言不存在」的守门用例，实现前即通过（预期如此），用于防止后续误把平台投影加进成员路径。
      - 测试基建需在 `tests/test_identity_web_handlers.py` 的 `_app()` 中补注册 `/api/tenant` → `TenantInfoHandler`（此前未注册）。
- [x] 3.3 实现空间只读投影：标识取租户编码、可用状态取「目录存在且通过既有跨租户包含校验」、隔离类型为固定标识；仅在平台侧读取路径填充
      - `channel/web/admin_handlers.py` 新增 `TENANT_SPACE_ISOLATION`、`_tenant_space_public`、`_tenant_platform_public`；`PlatformTenantHandler.GET` 改用平台投影，`TenantInfoHandler` 保持 `_tenant_public`。
      - 校验显式传入 `svc=_get_service()`，避免在读取路径回落到全局 `get_identity_service()` 而访问环境默认库。
- [x] 3.4 确认 `list_tenants` 与 `_tenant_public` 既有「不返回宿主目录」的断言继续通过
      - 运行 8 个相关测试模块：167 passed（含 `tests/test_tenant_create_containment.py`、`tests/test_state_dir_tenant_containment.py`）。

## 4. 前端：租户整页编辑器骨架

- [x] 4.1 先写失败用例（`node:test` + `vm`，沿用 `tests/test_tenant_create_frontend.cjs` 模式）：编辑器可在 `tenant` 视图打开，含顶栏标识与四个标签，默认激活「基本信息」
      - 新增 `tests/test_tenant_tabbed_editor_frontend.cjs`；用例 `editing a tenant opens the full-page tabbed editor, not the modal` 断言 `editorTabs()===['basic','model','tool','admin']`、默认 `basic`、顶栏 `#tenant-editor-sub==='acme'` 且共享弹窗未打开。
- [x] 4.2 先写失败用例：切换标签保留未提交输入并提示未保存；离开编辑器前触发未保存提示
      - 用例 `switching tabs keeps uncommitted input and flags the draft unsaved`、`leaving the editor with an unsaved draft asks before discarding`、`a clean editor leaves without prompting`。
- [x] 4.3 在 `channel/web/chat.html` 的 `#view-tenant` 内新增编辑器容器（顶栏 + 标签条 + 面板 + 底部保存栏），不改视图标识、菜单顺序与面包屑
      - 与既有角色编辑器同构：容器由 `ensureTenantEditor()` 运行时创建并挂进既有 `#view-tenant`（`ensureTenantEditor` 内 `view.appendChild(el)`），`channel/web/chat.html` 未新增静态标记（`#role-editor` 同样是运行时创建）。
      - 视图标识 `tenant`、菜单顺序与面包屑未改动；容器类名同时带 `role-editor` 与 `tenant-editor`，便于复用与覆盖。
- [x] 4.4 在 `channel/web/static/css/console.css` 以既有词汇新增编辑器样式，优先复用 `.role-editor-*` 类，不整段复制样式
      - 编辑器直接复用 `.role-editor` / `.role-editor-head|back|title-row|tabs|tab|body|panel|block-title|foot` / `.role-res-picker` / `.role-dirty-pill` / `.agent-field*`。
      - 仅追加租户专有规则：`.tenant-editor .role-editor-panel` 宽度、`.tenant-editor-tab:disabled` 创建态、`#tenant-space-card .grid > div` 只读卡片（含 `.dark` 变体）。未复制任何整段样式。
- [x] 4.5 实现标签切换与草稿保留，复用角色编辑器的切换契约
      - `switchTenantTab` 复用 `.role-editor-tab.active` + `.role-editor-panel.active` 契约；草稿按标签记在 `_tenantEditor.dirty`，`markTenantDirty` / `_tenantEditorClearDirty` 驱动 `#tenant-dirty-pill`。
- [x] 4.6 在 `channel/web/static/js/console.js` 的三套语言字典补充标签与提示文案键，不删除既有键
      - `zh` / `zh-Hant` / `en` 各补 19 键：`tenant_tab_{basic,model,tool,admin}`、`tenant_tab_{basic,model,tool,admin}_hint`、`tenant_space_{title,hint,id,status,ready,not_ready,isolation,unavailable}`、`tenant_editor_{conflict,create_hint,created_hint}`。
      - `node --check channel/web/static/js/console.js` 通过；既有键未删除。

## 5. 前端：基本信息标签

- [x] 5.1 先写失败用例：编辑模式提交 `{operation:"profile", name, active, expected_version, recent_password}`，且启用开关的变化确实进入请求体
      - 用例 `saving basic information posts the profile operation with the enabled flag`：断言 `operation==='profile'`、`active===false`（开关确实进请求体）、`expected_version===4`。
- [x] 5.2 先写失败用例：新建模式编码可填；编辑模式编码为只读且不进入请求体
      - 用例 `create mode can edit the code and disables the other tabs`（`tenant-fld-code.readOnly===false`）与 `create mode posts code/name/recent_password and stays on the editor`；编辑态 profile 请求体键为 `operation/name/active/expected_version/recent_password`，不含 `code`。
- [x] 5.3 先写失败用例：409 时保留草稿并提示重新加载；未确认提交成功不得显示保存成功
      - 用例 `a version conflict keeps the draft and never reports success`：`#tenant-editor-error` 为 `tenant_editor_conflict`、名称草稿 `Acme Renamed` 保留、`#tenant-dirty-pill` 仍为 `show`、`#tenant-status` 不含 `admin_saved`。
- [x] 5.4 实现基本信息标签（编码 / 名称 / 启用 / 近期密码），创建与编辑共用同一实现
      - `_submitTenantBasic` 单点承载创建与编辑；编辑走 2.6 的 `set_tenant_profile`（`operation:'profile'`），创建走既有 `POST /api/platform/tenants`。
- [x] 5.5 实现保存成功后顶栏启用徽标与版本号就地刷新
      - 用例 `the top bar refreshes its enabled badge and version after a save`：`#tenant-editor-active-badge` 文案与配色、`#tenant-editor-version` 就地更新，无需重开编辑器。
- [x] 5.6 新建模式下其余三个标签禁用并呈说明态，且不发出写入请求
      - 用例 `create mode can edit the code and disables the other tabs`：三个标签 `disabled===true`，且仅打开创建态时 `calls` 中不存在 POST/PUT；底部提示为 `tenant_editor_create_hint`。

## 6. 前端：模型授权与工具授权标签

- [x] 6.1 先写失败用例：模型标签以 `kind=model` 拉取平台 all-mode 目录，提交 `model.read` 与 `model.use`
      - 用例 `the model tab loads the model catalog and grants read+use`：目录请求带 `kind=model`，PUT 体含 `provider:deepseek:deepseek-v4-flash|read` 与 `|use`。
- [x] 6.2 先写失败用例：工具标签以 `kind=tool` 拉取目录，提交 `tool.read`、`tool.execute` 与 `tool.configure`
      - 用例 `the tool tab loads the tool catalog and grants read+execute+configure`，断言动作集为 `['configure','execute','read']`。
- [x] 6.3 先写失败用例：两个标签的候选集来自平台完整目录，不受该租户现有上限过滤
      - 用例 `grant candidate sets come from the platform catalog, not the tenant limits`：未授权的 `provider:openai:gpt-4o` 仍作为候选项出现。
- [x] 6.4 先写失败用例：两标签独立提交互不影响；上限提交 409 时保留勾选草稿
      - 用例 `the model and tool tabs save independently without clobbering each other`（第二次保存仍携带上一 kind 的 grants，`expected_version` 由 4 递增到 5）与 `a conflicted grant save keeps the checked draft`。
- [x] 6.5 把 `tenantModelGrantHtml` / `_initTenantModelGrant` / `_collectTenantModelGrants` 泛化为按 `resource_kind` 参数化，模型与工具共用
      - `tenantModelGrantHtml` → `tenantGrantHtml(resourceKind, actions)`；`_initTenantModelGrant` → `initTenantGrant(kind)`；原单例 `_tenantGrantSel/Catalog/Loaded/ApiBase/ResourcesBase/Version` 收敛为 `_tenantGrantState[kind]`。
- [x] 6.6 实现两个标签面板，复用搜索、分页、按页全选、清空与已选计数
      - `_ensureTenantGrantTab(kind)` 复用既有 grant picker 的搜索、分页、按页全选、清空与计数。
- [x] 6.7 验证既有模型上限行为等价（原模型授权相关用例继续通过）
      - 见 8.1：`tests/test_tenant_*.cjs` 与 `tests/test_identity_admin_frontend.cjs` 全绿，原模型授权路径未回归。

## 7. 前端：租户管理标签

- [x] 7.1 先写失败用例：标签内用账号选择器选出目标账号并提交其稳定标识给 `POST /api/platform/tenants/{id}/admins`；未选出时阻止提交且不发请求
      - 用例 `the admin tab lists platform accounts and posts the picked stable id` 与 `the admin tab refuses to submit while no account is picked`；未选中时错误为 `admin_user_picker_required`、`adminPosts().length===0`、`#tenant-dirty-pill` 保持 `show`。
- [x] 7.2 先写失败用例：标签内呈现租户空间只读信息卡（标识 / 状态 / 隔离类型），不出现宿主路径
      - 用例 `the admin tab renders the tenant space read-only and never a host path`（断言不含 `shared_root` 与 `/srv/tenants`）、`a space that is not ready is labelled as such`、`a tenant without a space projection says so instead of guessing`。
- [x] 7.3 复用现有 `userpicker` 与其字段收集逻辑，嵌入编辑器面板
      - `_ensureTenantAdminTab` 复用 `_userPickerHtml` + `_initUserPicker`（`onSelect` 标记 admin 标签为脏），提交沿用既有 `POST /api/platform/tenants/{id}/admins`。
- [x] 7.4 实现租户空间只读信息卡
      - `_renderTenantSpace` 以 `#tenant-space-card` 渲染标识 / 状态 / 隔离类型三格；数据来自 3.3 的平台侧只读投影。
- [x] 7.5 移除租户列表行上的管理员配置按钮，仅保留编辑入口；同步更新 `tests/test_tenant_create_frontend.cjs` 的对应断言
      - 行内仅保留 `adminRowAction('tenant','edit',…)`（`channel/web/static/js/identity-admin.js:1147`）；`adminRowAction` 的 `'admin'` 分支改为进入编辑器。
      - `tests/test_tenant_create_frontend.cjs` 已改写为断言无 `'tenant','admin'`、无 `'tenant','roles'`、保留 `'tenant','edit'`。
      - 追加发现（原计划未列）：`tests/test_tenant_admin_account_picker.cjs` 原本经 `adminRowAction('tenant','admin',…)` 打开弹窗，入口移除后 9 条用例全部失败；已把宿主改为编辑器「租户管理」标签，原有断言点（候选播种、稳定标识提交、未选中阻止、加载失败、空集合、搜索重查、乱序响应丢弃、截断提示）全部保留并通过。
      - 同步把编辑器 admin 标签的必填错误由 `admin_field_admin_user_id_hint` 改为 `admin_user_picker_required`，与选择器自身的必填契约保持一致。
- [x] 7.6 实现「创建成功后停在基本信息标签并提示跳转租户管理标签」，同步更新 `simplify-tenant-creation-form` 引入的接续用例断言，避免两处对同一行为作出相反断言
      - `_submitTenantBasic` 创建成功后调用 `openTenantEditor('edit', 新租户)`：停在「基本信息」、编码转只读、其余标签恢复可用、底部提示 `tenant_editor_created_hint`；用例 `after creation the editor becomes editable on the basics tab and points at the other tabs`。
      - 原「创建成功链入管理员弹窗」的两条接续用例（`create success chains into the tenant admin dialog`、`binding the first admin posts to the new tenant`）随弹窗移除而删除，其意图由上述用例与 7.1 的选择器用例承接；`tests/test_tenant_create_frontend.cjs` 文件头已注明新契约。

## 8. 验收、迁移核对与文档

- [x] 8.1 全量运行 `tests/test_identity_*.py` 与 `tests/test_tenant_*.cjs`，确认无回归，并保存输出
      - `node --test tests/test_tenant_admin_account_picker.cjs tests/test_tenant_create_frontend.cjs tests/test_tenant_tabbed_editor_frontend.cjs tests/test_identity_admin_frontend.cjs tests/test_nav_area_frontend.cjs tests/test_sidebar_account_frontend.cjs` → `94 pass / 0 fail`。
      - `.venv/bin/python -m pytest tests/test_identity_*.py tests/test_tenant_*.py … -q` → `146 passed`。
      - 全量套件（工作区）`33 failed, 1965 passed`；HEAD 基线 `30 failed, 1932 passed`。多出的 4 条已归因到工作区其他在途改动（见 `evidence.md` 第 5 节：最小复现 `test_memory_global_config.py` + `test_todo_service.py`，且**单独回退本 change 的两个源文件后仍复现**），本 change 无回归。
- [x] 8.2 端到端手工验证：新建租户 → 基本信息保存 → 模型授权 → 工具授权 → 指定管理员 → 停用 → 恢复，逐项核对版本递增与审计事件
      - `/tmp/e2e_tenant.py` 以真实 `web.application` + 临时 `identity.db` 跑完整序列：`16/16 checks passed`。
      - 版本：create=1 → 首次保存被 `no_admin` 整体拒绝（版本不变）→ 指定管理员 → 改名后 version=2 → 停用 version=5 → 恢复 version=6；每次被接受的保存恰好 +1 且恰好 1 条 `tenant.set_profile`；被拒绝的保存不写审计。
      - 口径说明：为 handler 级端到端（真实路由 + 真实服务 + 真实 SQLite），**不是浏览器操作**；浏览器视觉验收仍未做，已记入 `evidence.md` 第 4 节。
- [x] 8.3 确认本 change 不引入运行开关：没有 feature flag 或 `staged`/`enforced` 门控，能力随代码发布直接生效
      - `git diff` 新增行中无 `os.environ` / `getenv` / flag 门控；见 `evidence.md` 第 6 节。
- [x] 8.4 核对租户列表显示与 `tenants.active` 的一致性，列出历史不一致项交运维确认；本次不做自动数据修复
      - 对现有 `identity.db` 只读核对：`default`/`test01`/`test02` 三租户均 `active=1`。
      - **1 项不一致**：`test02`（test02租户）`active=1` 但有效 `tenant_admin` 数为 0。已记入 `evidence.md` 第 7 节交运维确认，本次不做自动修复。
      - 同时记录其运行时后果：该租户在启用态下保存「基本信息」会返回 409 `no_admin`，需先指定管理员或先停用。
- [x] 8.5 撰写 `evidence.md`：记录命令、原始输出与手工验证结论，明确区分「已验收」与「未测试」，不把产物完成当作实现完成
      - 新增 `openspec/changes/tenant-tabbed-editor/evidence.md`：含前端/后端定向用例输出、断言敏感性变异核验、handler 级端到端 16/16、全量回归差异归因、运行开关核对、真实库一致性核对。
      - 「未测试」单列一节：浏览器视觉与交互、真机多会话冲突演练、`test_memory_global_config.py` 引起的历史串扰、PRD 原文逐条核对。
- [x] 8.6 记录 PRD 回填项：PRD-01～12 原文恢复后核对租户控制台是否另有标签划分、字段清单或工具上限动作集要求
      - 已记入 `evidence.md` 第 8 节：待复核三处口径（四标签划分、模型动作集 `read`+`use`、工具动作集 `read`+`execute`+`configure`）。
