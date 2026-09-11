## 1. 测试先行：锁定选择器契约

- [ ] 1.1 新建 `tests/test_tenant_admin_account_picker.cjs`（沿用 `tests/test_tenant_create_frontend.cjs` 的 `node:test` + `vm` + `document`/`fetch` 桩模式），先写「配置租户管理员对话框渲染候选选择器而非自由文本 `user_id` 输入」的失败用例，并确认其因功能缺失而失败（RED）。
- [ ] 1.2 新增用例：选择器以 `GET /api/platform/users?status=active` 初始化，候选渲染「显示名 · 用户名」，选中后 `POST /api/platform/tenants/{id}/admins` 的 `body.user_id` 为所选稳定 ID。
- [ ] 1.3 新增用例：未选择任何候选即提交时不发出配置请求，且界面出现可见错误。
- [ ] 1.4 新增用例：候选查询返回错误时显示可重试错误、保留对话框、不发出配置请求，且不显示保存成功。
- [ ] 1.5 新增用例：有效账号为空时呈现空态而非错误，并同样阻止提交。
- [ ] 1.6 新增用例：在搜索框输入条件时以 `q` 参数重新查询，且并发响应的旧结果不覆盖最新结果。
- [ ] 1.7 新增用例：候选总数超过单次加载上限时显示「结果已截断」提示。
- [ ] 1.8 确认 `tests/test_tenant_create_frontend.cjs` 既有断言在改动后仍成立：该文件断言 `adm-fld-user_id` 字段存在（用例 `create success chains into the tenant admin dialog`），新实现的容器 id 必须仍为 `adm-fld-user_id`；其 `fetch` 桩未覆盖 `/api/platform/users`，需为该桩增加候选响应，使既有用例在候选加载成为对话框初始化的一部分后仍可解析。
- [ ] 1.9 RED 阶段全部用例失败原因记录为「功能缺失」，而非桩/语法错误。

## 2. 渲染层：新增 `userpicker` 字段类型

- [ ] 2.1 在 `channel/web/static/js/identity-admin.js` 的 `fieldHtml()` 增加 `userpicker` 分支，渲染候选容器、搜索框、加载态与错误态占位；容器 id 固定为 `adm-fld-<name>` 以复用既有脏标记与必填逻辑。
- [ ] 2.2 在 `collectField(f)` 增加 `userpicker` 分支，返回选择器状态中的 User ID（未选中时返回空值），不得回退读取输入框 `value`。
- [ ] 2.3 在 `openAdminModal()` 的字段初始化循环中，对 `userpicker` 调用 `_initUserPicker(node)`（与 `resourcegroup` / `modeldefaults` 并列）。

## 3. 选择器行为：候选加载、搜索与失败语义

- [ ] 3.1 实现 `_initUserPicker(node)`：以 `GET /api/platform/users?status=active&page_size=100` 加载候选，渲染「显示名 · 用户名」，展示态更新选中项并触发 `markModalDirty()`。
- [ ] 3.2 实现搜索输入（复用 300ms 去抖）以 `q` 参数在服务端过滤，并为选择器自身维护请求序号：仅应用最新一次请求的结果，丢弃先前请求的迟到响应（既有 `apiFetch` 的 `stale-response` 只覆盖跨租户切换的 `_generation`，不覆盖同租户内的请求乱序）。
- [ ] 3.3 区分三种非正常态并分别渲染：加载中、可重试错误、空候选；错误与空候选均使选择器状态为空值以阻止提交。
- [ ] 3.4 当 `total` 大于单次返回条数时显示「结果已截断，请用搜索缩小范围」提示。
- [ ] 3.5 候选标注仅展示账号本身信息（显示名、用户名、平台管理员标识），不请求也不展示跨租户成员关系。

## 4. 接入租户管理员对话框

- [ ] 4.1 将 `openTenantAdmin(id)` 的 `user_id` 字段由 `type: 'text'` 改为 `type: 'userpicker'`，保留 `required: true`；`display_name` 与 `recent_password` 字段语义不变。
- [ ] 4.2 确认 `recent_password` 二次授权、`expected_version`/冲突重载与 `afterSuccess` 接续流程（创建租户后自动打开该对话框）均未受改动影响。
- [ ] 4.3 确认选择器的候选读取复用既有 `apiFetch` 封装与错误约定，非平台管理员在到达该对话框前已被拒绝，未新增权限面。

## 5. 文案：三套语言字典

- [ ] 5.1 在 `channel/web/static/js/console.js` 的简中/繁中/英文三套字典中新增选择器加载中、空态、加载失败、搜索占位与结果截断提示的文案键，保持三套语言键集一致。
- [ ] 5.2 保留 `admin_field_admin_user_id` / `admin_field_admin_user_id_hint` 既有键（其他视图复用），仅调整其在租户管理员对话框中的提示内容，不删除任何既有键。
- [ ] 5.3 确认未引入硬编码的中文/英文文案，全部经 `t()` 取值。

## 6. 文档与验收证据

- [ ] 6.1 运行新增前端用例与 `tests/test_tenant_create_frontend.cjs`、`tests/test_identity_admin_frontend.cjs`，确认全绿并记录命令与输出。
- [ ] 6.2 运行受影响 Python 测试集（`tests/test_identity_web_handlers.py`、`tests/test_platform_user_admin.py`、`tests/test_identity_service.py`），确认未因前端改动产生回归。
- [ ] 6.3 在 `openspec/changes/tenant-admin-account-picker/` 补充 `evidence.md`，记录实际执行命令、用例名与结果，不把接口就位当作已验收。
- [ ] 6.4 手工或在浏览器中验证：为无管理员的租户（如 `test02`）打开配置对话框 → 搜索并选中一个有效账号 → 提交成功 → 租户列表显示该管理员，且不再出现 `user not found or disabled`。
- [ ] 6.5 记录范围缺口：系统仍无「平台新建账号」入口，全新部署若可绑定账号不足，需后续 change 处理。
- [ ] 6.6 归档前执行 `openspec validate tenant-admin-account-picker --strict`，确认 delta 生效且任务全部勾选。
