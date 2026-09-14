## Why

租户管理员（`test15` /「AI启航团队」）在「成员管理」页新建成员，填好用户名、显示名与 6 位临时密码后点击「创建」，弹窗底部只显示 `load-failed`：既看不出失败原因（是密码太短、用户名被占用，还是服务端坏了），表单也留在原地，无法判断下一步该改什么。

排查结论（证据见 tasks §1）：

1. **服务端把可纠正的校验失败变成了 500**。`IdentityService.create_member` 在**校验之前**就调用 `hash_password(temporary_password)`；`auth.password._check_password` 对长度不足（< `MIN_PASSWORD_LENGTH` = 8）或全空白的密码抛出 `PasswordError`。该异常**不是** `IdentityServiceError`，而 `TenantMembersHandler.POST` 只翻译 `IdentityServiceError`，于是它一路逃逸出 handler，被 `web.py` 兜成一份 500 的 **HTML** 错误页（`nohup.out` 中的 `PasswordError: password must be at least 8 characters` 即此）。
2. **前端把非 JSON 的失败折叠成无信息的 `load-failed`**。`identity-admin.js` 的 `apiFetch` 先 `resp.json().catch(() => ({}))`，HTML 响应体解析失败得到 `{}`，再以 `data.message || 'load-failed'` 抛错；`submitAdminModal` 对该错误直接 `showAdminErr(err.message)`。因此原因被确定性地抹掉——这既是本次现象的成因，也是同类 500 在过去已被记录过一次的模式（见 `tests/test_identity_web_handlers.py` 中「the console rendered as the generic "load-failed"」的回归注释）。
3. 与既有规范的差距：`identity-management-workbench` 已要求「失败时保留可修正的输入且不显示成功」，但**没有**要求「用户可纠正的校验失败必须以结构化错误返回、并呈现可操作原因」。仅靠「不显示成功」不足以让管理员知道要改什么。

同一缺陷面还包括**全空白密码**（长度 ≥ 8 但 `strip()` 为空）：`_validate_new_account` 不拦，`hash_password` 抛 `PasswordError: password must not be blank`，同样 500。

## What Changes

- **服务端把密码校验失败翻译为结构化拒绝**：`create_member` 在计算临时密码哈希处捕获 `PasswordError`，转换为与事务内 `_validate_new_account` 同口径的 `IdentityServiceError("weak temporary password", code="weak_password", status=400)`。覆盖长度不足与全空白两种当前会 500 的输入，并保留既有错误优先级（用户名冲突的判定顺序不变）。做法沿用 `auth/service.py` 既有模式（`sqlite3.IntegrityError` → `IdentityServiceError`）。
- **前端按服务端错误码给出可操作原因**：`submitAdminModal` 的失败分支在回退到原始 message 之前，先复用租户编辑器既有的 `_tenantAdminNewReason()` 映射——`weak_password` → `tenant_admin_weak_password`（「初始密码不满足强度要求：至少 8 位，且不要使用常见口令。」），`invalid_username` → `tenant_admin_invalid_username`。无新 i18n 键，三语系无需改动。
- **写入弹窗失败时保留底层证据**：失败分支把 HTTP 状态与服务端 code/message 记入控制台，使后续同类报告无需现场复现即可定位。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `identity-management-workbench`: 「用户页面支持可检索的完整成员生命周期」补充**失败可操作性**——用户可纠正的创建失败（弱/空白临时密码、非法用户名）MUST 以结构化 4xx 错误返回、MUST NOT 逃逸为 500；成员创建弹窗 SHALL 呈现与该服务端错误码对应的可操作文案，MUST NOT 折叠为无信息的通用失败文案，并 SHALL 记录底层证据。

## Impact

- 服务端：`auth/service.py`（`create_member` 的哈希处翻译 `PasswordError`；导入 `PasswordError`）。
- 前端：`channel/web/static/js/identity-admin.js`（`submitAdminModal` 失败分支按 code 映射文案并记录证据；复用既有 `_tenantAdminNewReason`，不新增键）。
- i18n：无新增键；不动 `console_i18n_snapshot.json`。
- 测试：`tests/test_identity_service_writes.py`、`tests/test_identity_web_handlers.py`、`tests/test_identity_admin_frontend.cjs`。
- 不改动：`/api/tenant/members` 的路由与授权、角色绑定、个人助理开通、`update_member` 契约、成员列表与分页。

### 明确不在范围内

- **`apiFetch` 对任何非 JSON 响应的通用兜底**：本次只在**成员创建**这条链路上消除「500 + HTML + 通用文案」；其余管理视图的 `apiFetch` 兜底语义保持不变（与 `fix-agent-workbench-failure-reporting` 只约束列表读取同理）。真正未知的 5xx 仍会呈现通用失败文案，但本 change 已保证用户可纠正的输入不再落入该路径。
- **客户端密码强度预校验**：不在密码输入框上加 `minlength` 等前端拦截。服务端仍是权威，前端只负责把服务端的拒绝原因呈现清楚。
