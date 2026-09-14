## 1. 根因排查（先定位，再改）

- [x] 1.1 复现用户路径：`POST /api/tenant/members`（tenant `tnt_EA3qM-lHPLD8ZPwW`，`operation=create-new`，`temporary_password="123456"`）返回 **HTTP 500 + HTML 错误页**，响应体为 `web.py` 的 traceback 页
- [x] 1.2 从服务端输出定位异常：`nohup.out` 中 `auth/service.py:3875 in create_member → hash_password → auth/password.py:45 _check_password → auth.password.PasswordError: password must be at least 8 characters`
- [x] 1.3 确认调用顺序缺陷：`create_member` 在 `_validate_new_account`（事务内，会给出 `code=weak_password`）**之前**就调用 `hash_password`
- [x] 1.4 确认异常未被翻译：`TenantMembersHandler.POST` 只 `except IdentityServiceError`，`PasswordError` 逃逸出 handler
- [x] 1.5 确认前端抹掉原因：`apiFetch` 用 `resp.json().catch(() => ({}))` 解析 HTML 得到 `{}`，再以 `data.message || 'load-failed'` 抛错；`submitAdminModal` 直接显示该 message
- [x] 1.6 核对同类调用点：`create_tenant` / `create_tenant_admin` / `change_password` / `_set_password_for_user` 均在 `hash_password` 之前校验长度，**仅 `create_member` 顺序相反**；`reset_platform_user_password` 用服务端生成密码，天然安全
- [x] 1.7 确认第二种未拦截输入：长度 ≥ 8 但 `strip()` 为空的密码通过 `_validate_new_account`，仍在 `hash_password` 抛 `PasswordError: password must not be blank`

## 2. 回归测试（RED）

- [x] 2.1 `tests/test_identity_service_writes.py`：`create_member` 以 6 位密码调用时抛 `IdentityServiceError`（`code=weak_password`）而非 `PasswordError`（实现前失败：`auth.password.PasswordError: password must be at least 8 characters`）
- [x] 2.2 同文件：全空白密码（8 个空格）同样得到 `code=weak_password`（实现前失败：`PasswordError: password must not be blank`）
- [x] 2.3 `tests/test_identity_web_handlers.py`：HTTP 新建成员用 6 位密码返回 4xx 且 JSON `code=weak_password`、`status=error`（实现前失败：500 + HTML 正文）
- [x] 2.4 `tests/test_identity_admin_frontend.cjs`：成员创建被 `weak_password` 拒绝时，`admin-modal-error` 呈现 `tenant_admin_weak_password` 文案且弹窗不关闭（实现前失败：呈现原始 `weak temporary password`）

## 3. 实现（GREEN）

- [x] 3.1 `auth/service.py`：导入 `PasswordError`；`create_member` 计算临时密码哈希处捕获 `PasswordError`，转换为 `IdentityServiceError("weak temporary password", code="weak_password", status=400)`（与事务内 `_validate_new_account` 同口径；错误优先级不变）
- [x] 3.2 `identity-admin.js`：`submitAdminModal` 失败分支按服务端 `code` 复用 `_tenantAdminNewReason()` 呈现可操作文案，未识别时回退服务端 message，再回退通用文案
- [x] 3.3 `identity-admin.js`：失败分支把 HTTP 状态与服务端 code/message 记入控制台（`[admin-write] failed: http 400 weak_password …`）

## 4. 验证

- [x] 4.1 `python -m pytest tests/test_identity_service_writes.py tests/test_identity_web_handlers.py tests/test_identity_service.py tests/test_identity_password.py tests/test_tenant_read_scoping.py tests/test_identity_scope_gate.py -q`：**170 passed**
- [x] 4.2 `node --test tests/test_identity_admin_frontend.cjs`：**27 passed**；`tests/test_tenant_tabbed_editor_frontend.cjs` / `test_tenant_admin_account_picker.cjs` / `test_console_i18n_parity.cjs` / `test_i18n_tenant_editor_keys.cjs`：**85 passed**
- [x] 4.3 端到端（重启后的真实服务 `127.0.0.1:9899`，租户 `tnt_EA3qM-lHPLD8ZPwW`）：6 位密码 → **HTTP 400** `{"status":"error","message":"weak temporary password","code":"weak_password"}`；8 个空格 → **HTTP 400** 同上；`/assets/js/identity-admin.js` 与工作树文件逐字节一致（含新映射）
- [x] 4.4 `node --check channel/web/static/js/identity-admin.js` 通过
- [x] 4.5 `openspec validate fix-member-create-weak-password-error --strict` 通过
- [x] 4.6 全量筛选回归（`-k "identity or member or password or auth"`）：**644 passed / 6 failed / 1 skipped**；6 项失败在**暂存本次改动后**同样失败（头像 2 项、legacy 模式 fail-closed 2 项、user_avatar 2 项），均为既有失败，本次改动**回归 0 项**

## 5. 收尾

- [x] 5.1 确认为未放宽任何授权或校验：仍拒绝弱密码与非法用户名，仅把拒绝从 500 改为结构化 400
- [x] 5.2 清理排查期间通过 API 创建的探测成员（`rock_probe_1` / `Rock` / `Rock2`）：membership/user/会话/角色绑定行已删除、个人助理绑定已释放、工作区目录已移除；租户成员恢复为 `test15` / `test15-2`，默认智能体仍为 `my-assistant-admin-test15`（清理前已备份 `identity.db.bak-probe-cleanup-*`；审计事件按追加式历史保留）
