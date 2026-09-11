# Tasks: 租户编辑器统一保存与新建租户管理员账号

约定：每组任务先写失败测试（RED）再实现（GREEN），未通过前不得勾选。每个验收组在进入下一组前 MUST 记录命令与原始输出。本 change 不引入 feature flag，无迁移任务。

## 1. 服务层：成员显示名可修改（修正既有缺陷）

- [x] 1.1 在 `tests/test_identity_service_writes.py` 增加失败测试：`set_tenant_admin` 对**已是有效成员**的账号提交不同 `display_name` 时，`memberships.display_name` 被更新且该成员 `version` 递增。
- [x] 1.2 增加失败测试：上述更新 MUST NOT 改变 `users.display_name`，也 MUST NOT 影响该账号在其他租户的成员显示名。
- [x] 1.3 增加失败测试：提交与现值相同的 `display_name` 时不递增成员版本、不产生多余变更。
- [x] 1.4 增加失败测试：停用成员恢复路径下提交 `display_name` 时同样生效。
- [x] 1.5 修改 `auth/service.py` 的 `set_tenant_admin`，在有效成员与恢复两条分支上按「仅在值不同时更新并递增版本」实现，使 1.1～1.4 通过。

## 2. 服务层：新建账号并绑定租户管理员（单事务）

- [x] 2.1 从 `create_member` 提取用户名与临时密码校验为共享私有函数，先跑既有 `create_member` 测试保证行为不变（重构，不改变契约）。
- [x] 2.2 在 `tests/test_identity_service_writes.py` 增加失败测试：新方法在同一次调用内建立账号、成员关系与 `tenant_admin` 绑定并写入审计。
- [x] 2.3 增加失败测试：新账号为 `active=1` 且 `must_change_password=1`，临时密码有效期沿用既有 3 天契约。
- [x] 2.4 增加失败测试：用户名已存在返回 409，且不建立任何成员关系或角色绑定。
- [x] 2.5 增加失败测试：初始密码过短或命中常用口令表时被拒绝，不建立账号。
- [x] 2.6 增加失败测试：非法用户名被拒绝。
- [x] 2.7 增加失败测试：角色绑定失败（例如租户缺少 `tenant_admin` 角色）时账号与成员关系整体回滚，MUST NOT 留下孤儿账号。
- [x] 2.8 增加失败测试：审计写入失败时整体回滚并返回 503。
- [x] 2.9 增加失败测试：新账号建立后，该租户的有效 `tenant_admin` 计数增加 1，且可满足随后「启用租户」的连续性校验。
- [x] 2.10 在 `auth/service.py` 实现新方法（建立账号 + 成员关系 + `tenant_admin` 绑定 + 审计，单事务），使 2.2～2.9 通过。
- [x] 2.11 增加测试：非平台管理员调用被拒绝（403），操作者 MUST NOT 因此获得该租户 Membership。

## 3. Handler 层：`mode` 分派

- [x] 3.1 在 `tests/test_identity_web_handlers.py` 增加失败测试：`mode` 缺省或 `existing` 时走既有绑定路径，行为与现状一致（向后兼容）。
- [x] 3.2 增加失败测试：`mode=new` 时按 `username` / `display_name` / `temporary_password` 调用新服务方法并返回成功。
- [x] 3.3 增加失败测试：未知 `mode` 返回 400 `invalid_request`，MUST NOT 静默降级为任一既有路径。
- [x] 3.4 增加失败测试：`mode=new` 缺少必填字段时返回 400 且不写入。
- [x] 3.5 修改 `channel/web/admin_handlers.py` 的 `PlatformTenantAdminsHandler.POST` 实现分派，使 3.1～3.4 通过。
- [x] 3.6 增加测试：写请求与响应 MUST NOT 回显 `temporary_password`。

## 4. 前端：统一保存与密码弹窗

- [x] 4.1 在 `tests/test_tenant_tabbed_editor_frontend.cjs` 增加失败测试：保存动作收集所有脏标签，并按「管理员配置 → 资源授权 → 基本信息」顺序串行提交。
- [x] 4.2 增加失败测试：每一步使用上一步响应回显的版本作为下一次 `expected_version`（版本串联），资源授权即使覆盖模型与工具也只发生一次 PUT。
- [x] 4.3 增加失败测试：某一步失败时停止后续步骤，已成功步骤的结果保留，未提交标签的脏标记保留，并显示失败步骤。
- [x] 4.4 增加失败测试：全部成功时清除所有相关脏标记并显示整体保存成功。
- [x] 4.5 增加失败测试：仅资源授权脏时不弹密码弹窗；包含基本信息或管理员配置脏时弹密码弹窗。
- [x] 4.6 增加失败测试：取消密码弹窗不发写请求且保留全部脏标记。
- [x] 4.7 增加失败测试：密码校验失败时弹窗保留、显示可重试错误，且草稿与已选账号不丢失。
- [x] 4.8 增加失败测试：保存成功后密码不再保留在 DOM 中，且未写入 `localStorage` / `sessionStorage`。
- [x] 4.9 增加失败测试：基本信息与租户管理面板中不再存在常驻 `recent_password` 输入字段。
- [x] 4.10 在 `channel/web/static/js/identity-admin.js` 实现统一保存、提交顺序、版本串联、失败即停与密码弹窗，使 4.1～4.9 通过。
- [ ] 4.11 实现密码弹窗层级与焦点管理，并在真实浏览器核对与全页编辑器叠加时不被遮挡、Esc 与取消行为一致。
      **部分完成**：Esc=取消、Enter 提交、空密码不放行已实现且有 DOM 桩用例（经变异核验）；层级以静态 CSS 推断（`z-index: 80` vs 编辑器 20，同处根堆叠上下文）**未在真实浏览器实测**。见 evidence.md §9.2 与 §10。

## 5. 前端：租户管理标签双模式

- [x] 5.1 增加失败测试：标签渲染「选择已有账号 / 明确创建新账号」模式切换，缺省为选择模式。
- [x] 5.2 增加失败测试：选择模式下显示名为可选，选中账号后以该账号显示名预填且允许修改；未选账号时阻止提交。
- [x] 5.3 增加失败测试：新建模式下用户名、显示名、初始密码为必填，任一为空时阻止提交并给出可操作提示。
- [x] 5.4 增加失败测试：新建模式提交时按 `mode=new` 组装请求体且不发送 `user_id`。
- [x] 5.5 增加失败测试：选择模式提交时按 `mode=existing` 组装请求体且发送 `user_id`。
- [x] 5.6 增加失败测试：切换模式不丢失另一种模式已填写的内容。
- [x] 5.7 增加失败测试：用户名冲突（409）时保留表单字段并提示可切换为选择该账号。
- [x] 5.8 增加失败测试：新建成功后清除该标签脏标记，候选账号集重新加载后可检索到新账号。
- [x] 5.9 在 `channel/web/static/js/identity-admin.js` 实现双模式与新建账号表单，使 5.1～5.8 通过。
      **实现**：`#tenant-panel-admin` 内新增 `.tenant-admin-mode` 切换与 `#tenant-admin-body-existing` / `#tenant-admin-body-new` 两个常驻面板（只切显隐，故切换天然不丢内容）；`setTenantAdminMode` / `_tenantAdminBody` / `_resetTenantAdminFields`；提交体按模式组装（`existing` 带 `user_id`、`new` 带 `username`/`display_name`/`temporary_password`，两者都带 `recent_password`）。选择模式选中账号后以 `data-user-name` 预填成员显示名且仍可改；新建成功后 `_refreshUserPicker` 重查候选集（保持已选 id），失败步骤带 `stepKey`/`stepAdminMode` 以便把 409 精确解释为「用户名已被占用」。**验证**：8 项新用例全绿（该文件 34 → 42 项）；9/9 定点变异全部 `CAUGHT`（见 evidence.md §5）。

## 6. 样式与文案

- [x] 6.1 在 `channel/web/static/css/console.css` 增加密码弹窗与模式切换样式，沿用既有 `role-editor-*` 视觉语言。
      **说明**：弹窗样式改为复用既有 `agent-modal` / `agent-modal-card` / `agent-modal-foot`，仅补 `.tenant-password-modal { z-index: 80 }` 及标题/底部/输入框微调（见 evidence.md §6）。模式切换补充 `.tenant-admin-modes` / `.tenant-admin-mode` / `.tenant-admin-mode.active`（含 dark 变体）。
- [x] 6.2 在 `channel/web/static/js/console.js` 的 `zh` / `zh-Hant` / `en` 三套字典补齐密钥：密码弹窗标题/提示/错误、模式切换标签、新建账号字段与校验提示、多标签保存结果与失败步骤提示。
      **说明**：密码弹窗与失败步骤 5 个密钥已补齐三套字典；组 5 再补 9 个（`tenant_admin_mode_existing|new`、`tenant_admin_new_username|display|password`、三个 `_required` 与 `tenant_admin_username_taken`），共 14 个 × 3 语言。新建账号字段标签经 `_localizeTenantEditorChrome` 的 `labels`/`modes` 映射注入。
- [x] 6.3 增加前端契约测试核对三套字典键集合一致，避免漏译。
      **说明**：新增 `tests/test_i18n_tenant_editor_keys.cjs`（4 项）；因 `console.js` 的字典由字面量 + 6 处 `Object.assign` 追加构成，测试以大括号配平扫描定位各语言区块，并对 `tenant_*`、`admin_user_picker*` 键族做跨语言一致性核对。组 5 的 9 个新键已加入其 `NEW_KEYS` 显式清单；变异核验：从 `en` 字典删掉 `tenant_admin_mode_new` 后该文件 2 项失败（`... is missing from the en dictionary`），恢复后 4/4 通过。见 evidence.md §6.3。

## 7. 既有用例迁移

- [x] 7.1 改写 `tests/test_tenant_tabbed_editor_frontend.cjs` 中断言「按标签独立保存」的用例，改为断言统一保存契约（不得删除以维持覆盖率）。
      **复核结论**：`the model and tool tabs save independently without clobbering each other` 执行的是两次独立保存（各一次 PUT，版本 4 → 5），在新契约下**依然成立**，故保留全部原断言，仅校正措辞。
- [x] 7.2 改写断言面板内存在 `recent_password` 字段的用例。
- [x] 7.3 复核 `tests/test_tenant_create_frontend.cjs` 与 `tests/test_tenant_admin_account_picker.cjs`：创建态与管理员配置的断言需与统一保存、双模式一致。
      **说明**：两文件的提交助手改为「触发保存 → 弹窗内确认密码」，并先派发 `input`/`change` 使标签进入脏集合；「未选账号不得提交」的两个用例改为先把该标签标脏，以免因「无脏标记即空操作」而变成恒真。共 4 + 9 项通过。
- [x] 7.4 全量运行 `tests/*.cjs` 并记录通过/失败数（含 `_tmp_repro_modeldefaults.cjs` 之外的 18 个文件）。
      **结果（组 5 完成后复测，逐文件实测）**：见下方 §7.4。

## 8. 验收与证据

- [x] 8.1 对新增断言做定点变异核验，确认每条断言可失败（记录变异与对应用例）。
      **结果**：组 4 的 10 项前端变异全部 `CAUGHT`；1 项后续判定为**语义无效**的变异已记录（见 evidence.md §4.1）；组 5 的 9 项变异全部 `CAUGHT`（含一条专为「切换模式不丢内容」设计的定向变异，见 §5.3）；i18n 契约的漏译断言经删键变异核验（§6.3）；后端版本递增断言亦经变异核验（§8.1）。
- [x] 8.2 用真实 `web.application` + 临时 `identity.db` 跑端到端序列：建号 → 绑定 → 统一保存（三标签一次提交）→ 版本递增核对 → 审计核对。
      **说明**：已固化为 `tests/test_identity_web_handlers.py` 的 4 项长期回归用例（原一次性脚本 `/tmp/e2e_batch_save.py` 曾 22/22 通过）。见 evidence.md §8.1。
- [ ] 8.3 在真实浏览器核对：模式切换、密码弹窗、统一保存的失败即停表现与最终成功表现。
      **未执行**：无平台管理员口令，未登录控制台。组 5 的双模式已实现并经 DOM 桩 + 变异核验，但视觉与真实键盘/鼠标行为仍未目视。见 evidence.md §9 与 §10（§10 已补入模式切换与新建账号的核对步骤）。
- [x] 8.4 运行 `openspec validate tenant-editor-batch-save-and-admin-create --strict`。
      **结果**：`Change 'tenant-editor-batch-save-and-admin-create' is valid`。
- [x] 8.5 编写 `openspec/changes/tenant-editor-batch-save-and-admin-create/evidence.md`，记录命令与原始输出，并明确列出未测试项。
- [x] 8.6 记录本次发现的既有数据一致性问题（如启用但无有效管理员的租户）交运维，不在本 change 内自动修复。
      **结果**：真实 `identity.db` 中 `test02` / `test03` / `test11` / `test12` 四个租户为「已启用但有效 `tenant_admin` 数为 0」；同时记录了成因（创建默认启用 vs 启用需有管理员）与运维待决策项。见 evidence.md §8.3。

## 9. 验收期发现的缺陷：创建账号失败原因未透出（已在验收过程中修复）

背景：验收组 5 时，操作者在真实控制台以 6 位初始密码提交「创建新账号」，保存失败，界面只显示「保存未完成，失败步骤：租户管理」，未说明原因。

- [x] 9.1 定位根因：`MIN_PASSWORD_LENGTH = 8`，6 位密码被服务端以 `code=weak_password`、`status=400` 拒绝；而 `_reportTenantBatchError` 只对 `409` 做解释，其余一律折叠为「失败步骤」。以临时库确定性复现（§11.1），并与真实库「失败路径不写审计」交叉印证。
- [x] 9.2 增加失败测试：`weak_password` / `invalid_username` 需分别给出针对性说明；且批量保存中夹带「基本信息」时该说明仍须保留、后续步骤仍须跳过。
- [x] 9.3 增加回归护栏测试：未被映射的错误码仍须指明失败步骤（防止修复把「有原因」变成「无提示」）。
- [x] 9.4 增加失败测试：初始密码字段须携带与既有账号创建入口**同一**强度提示文案。
- [x] 9.5 实现：新增 `_tenantAdminNewReason`，按服务端 `code` 映射（`weak_password` / `invalid_username` / `conflict`）后再回退到通用步骤提示；初始密码字段补 `agent-field-hint`，复用既有 `admin_field_password_hint`。
- [x] 9.6 补齐 `tenant_admin_weak_password` / `tenant_admin_invalid_username` 三套字典文案，并加入 i18n 契约测试的显式键清单。
- [x] 9.7 变异核验：7/7 `CAUGHT`（含「把未知码当作已知码吞掉」以验证通用回退仍被守住）。
- [x] 9.8 修改 spec delta：`租户管理员配置入口收在租户管理标签` 增加「须按错误码给出可操作原因、不得仅报失败步骤」及两个对应 Scenario。

## 10. 验收期发现的缺陷：强制改密门禁不可见（已在验收过程中修复）

背景：操作者新建的管理员账号（`test15`）首次登录时，点击「登录」无任何反应。

- [x] 10.1 定位根因：登录**已成功**（`/auth/login` 返回 200，`must_change_password: true`），但 `#account-password-modal` 位于 `#app` 之内，而 `_enterForcedPassword()` 恰好执行 `_accountHidden('app', true)`；被隐藏的祖先把弹窗折叠为 **0×0**（实测 `getBoundingClientRect()` 宽高均为 0），于是密码被清空、界面停在登录页、无报错，表现即「点登录没有反应」。
- [x] 10.2 确认影响面：`create_tenant_admin_account`（`auth/service.py:2116-2118`）硬编码 `must_change_password=1`，故本项目「创建新账号」产出的每个账号首次登录都会踩到该门禁；真实库中 `test01-admin`、`test15` 均为该状态。
- [x] 10.3 确认归属：`git diff` 证明 `_enterForcedPassword` 与弹窗结构均非本次改动，属既有缺陷；`self-password-flow` 既有需求已要求「登录和刷新 SHALL 识别 must_change_password … 展示同一表单」，故为**实现违反既有需求**。
- [x] 10.4 增加失败测试：`tests/test_forced_password_gate.cjs` 直接解析真实 `chat.html` 的标签层级，断言弹窗不在 `#app` 内、仍位于 body 内，并要求 `#login-overlay` 与门禁同为顶层浮层。
- [x] 10.5 增加回归护栏测试：断言 `_enterForcedPassword()` 仍隐藏 `#app`（保留既有意图），从而把「隐藏 app」与「门禁可见」这一对约束固定下来。
- [x] 10.6 实现：把 `#account-password-modal` 从 `#app` 内移至 body 层（`#login-overlay` 之后、`#app` 之前），并注明不可移回的原因。
- [x] 10.7 真实浏览器验证：重载后门禁 `modalRect` 由 `0×0` 变为 `1920×1080`（卡片 `460×406`），`parentElement === document.body`，`#app` 仍为 `display:none`，首个输入可聚焦，标题为「请设置新密码」。
- [x] 10.8 回归验证：普通「修改密码」弹窗在 `#app` 可见时仍正常渲染（`1920×1080`）；全量前端扫描 `203 pass / 8 fail`，失败集合不变。
- [x] 10.9 新增 spec delta `specs/self-password-flow/spec.md`：在既有需求上补入「门禁须为顶层浮层、不得位于受限期间会被隐藏的容器内」及 Scenario「强制改密门禁在业务界面隐藏时仍然可见」。

## 11. 验收期新增需求：打开「租户管理」标签即只读显示当前管理员

背景：验收时发现 `test15` 已有租户管理员，但打开「租户管理」标签时选择器为空、显示名为空，操作者看不到当前管理员是谁，容易误以为尚未指定。本组不改变保存契约，只补一个只读展示与支撑它的读取能力。

- [x] 11.1 在 `tests/test_identity_service_writes.py` 增加失败测试：`tenant_admins` 只返回「成员关系有效 + 账号有效 + 持有 tenant_admin 角色」三者同时成立的成员，按最早绑定排序，投影字段仅含成员关系标识、账号标识、登录名与成员显示名。
- [x] 11.2 增加失败测试：排除已停用账号、已停用成员关系与非 tenant_admin 成员；无管理员或未知租户返回空列表而非报错。
- [x] 11.3 在 `auth/service.py` 实现 `tenant_admins(tenant_id)`，使 11.1～11.2 通过。
- [x] 11.4 在 `tests/test_identity_web_handlers.py` 增加失败测试：`GET /api/platform/tenants/{id}/admins` 仅平台管理员可读，未知租户返回 404，响应不含密码、哈希或令牌。
- [x] 11.5 在 `channel/web/admin_handlers.py` 的 `PlatformTenantAdminsHandler` 实现 `GET`，使 11.4 通过。
- [x] 11.6 在 `tests/test_tenant_tabbed_editor_frontend.cjs` 增加失败测试：打开标签即只读显示最早绑定的当前管理员（成员显示名 + 登录名），账号选择器保持未选中、成员显示名输入保持为空。
- [x] 11.7 增加失败测试：租户无管理员时显示「尚未指定」；读取失败时显示可区分于「无管理员」的提示，且不阻止指定管理员。
- [x] 11.8 增加失败测试：该读取不使标签进入未保存状态；保存成功后只读展示刷新为已提交管理员。
- [x] 11.9 在 `channel/web/static/js/identity-admin.js` 实现只读展示块、`_loadTenantCurrentAdmin` 与保存后刷新，使 11.6～11.8 通过；`channel/web/static/css/console.css` 补样式。
- [x] 11.10 在 `channel/web/static/js/console.js` 三套字典补齐 `tenant_current_admin_*` 键，并加入 `tests/test_i18n_tenant_editor_keys.cjs` 显式键清单。
- [x] 11.11 定点变异核验每条新断言，并在 `evidence.md` 记录命令与原始输出。
      **结果**：后端 2/2 `CAUGHT`（`u.active=1` 过滤、投影泄漏凭据），前端 4/4 `CAUGHT`（读取失败误报为无管理员、读取时预选/预填、保存后不刷新、读取置脏）。另修正一处**弱断言**：`test_admins_get_requires_platform_admin` 原用受限（须改密）账号，403 来自上下文解析而非平台管理员校验；已改为清除该门禁后再断言，变异后确认 `CAUGHT`。见 evidence.md §12。

- [x] 11.12 修复验收期缺陷：`GET /api/platform/tenants/{id}/admins` 被 HTTP 方法策略门禁以 405 拒绝（`auth/http_policy.py` 该路由只登记 `POST`）。先写失败测试（`test_tenant_admins_read_registered` / `test_tenant_admins_get_reaches_handler_through_real_app`），再补 `GET` 登记；重启后 GET 由 405 变 401、`DELETE` 仍 405（门禁未被放宽）。`134 passed`。见 evidence.md §14。
      **复盘**：前一轮「仅需重启服务」的结论不完整——重启解决了进程陈旧，但主因是该策略表未同步登记新方法。此为本 change 的测试盲区：`test_identity_web_handlers.py` 自建裸 `web.application`，未安装 `enforce_http_policy`，故只覆盖 handler、覆盖不到真实门禁；新护栏改经 `build_web_app()` 断言。
