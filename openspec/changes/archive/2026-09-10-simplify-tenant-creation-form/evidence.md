# 测试与运行证据

对应 change `simplify-tenant-creation-form`，实施时间 2026-09-10。解释器为本仓库 `.venv`（Python 3.14.3），前端用例用 `node --test`。

## 1. 变更点

| 文件 | 变更 |
| --- | --- |
| `auth/service.py` | `create_tenant()` 的 `admin_username` / `admin_display` / `admin_password` 改为可选；以「用户名+密码同时非空」判定是否建立初始管理员；仅在该判定为真时校验弱密码、计算哈希并写入 `users` / `memberships` / `membership_roles` |
| `channel/web/admin_handlers.py` | `PlatformTenantsHandler.POST` 只读取 `code` / `name` / `recent_password`，忽略旧前端残留的 `admin_*` 字段 |
| `channel/web/static/js/identity-admin.js` | `openTenantCreate()` 字段缩减为 3 个；`openAdminModal()` 新增 `afterSuccess` 回调并在 `closeAdminModalNoPrompt()` 之后触发；创建成功后接续 `openTenantAdmin(id)` |

## 2. 前端 RED → GREEN 证据

新增 `tests/test_tenant_create_frontend.cjs`（5 个用例）。把该文件指向 `git show HEAD:channel/web/static/js/identity-admin.js`（改动前脚本）单独运行，5 个用例全部失败，证明用例确实约束了新行为：

```
$ node --test test_tenant_create_frontend.cjs      # 指向 HEAD 版本脚本
✖ tenant create form carries no admin account fields
✖ submitting only code/name/recent_password creates the tenant
✖ create success chains into the tenant admin dialog
✖ binding the first admin posts to the new tenant
✖ create failure does not open the admin dialog
ℹ pass 0
ℹ fail 5
```

指向当前脚本则全部通过：

```
$ node --test tests/test_tenant_create_frontend.cjs
✔ tenant create form carries no admin account fields
✔ submitting only code/name/recent_password creates the tenant
✔ create success chains into the tenant admin dialog
✔ binding the first admin posts to the new tenant
✔ create failure does not open the admin dialog
ℹ tests 5
ℹ pass 5
ℹ fail 0
```

其中「create success chains into the tenant admin dialog」断言 `afterSuccess` 在对话框关闭后打开第二个对话框（标题 `admin_tenant_admin_edit`、含 `adm-fld-user_id`、不含 `adm-fld-admin_password`），覆盖 design 中「顺序不可颠倒」的风险。

## 3. 服务层与 handler 证据

新增 `ServiceTenantCreateWithoutAdminTests`（`tests/test_identity_service.py`，7 个用例）与 4 个 handler 用例（`tests/test_identity_web_handlers.py`）：

```
$ .venv/bin/python -m unittest tests.test_identity_service
Ran 17 tests ... OK        # 含新增 7 个
```

覆盖：裸建租户无管理员/无 membership（`_count_valid_tenant_admins == 0`、`list_members total == 0`）、内置角色与 `__root__` 组织根仍建立、`recent_password` 仍强制、显式 `admin_*` 仍建管理员、显式弱密码仍被拒、code 冲突仍 409、无管理员租户可停用但恢复被 `no_admin`(409) 拒绝、绑定后恢复成功。

```
$ .venv/bin/python -m unittest tests.test_identity_web_handlers
Ran 56 tests ... OK        # 含新增 4 个
```

handler 用例直接断言：请求体只含 `code`/`name`/`recent_password` 时创建成功且响应与响应正文中不含 `admin_username` / `admin_password` / `temporary_password`；携带 `admin_username="attacker"` 的旧前端请求不会创建该账号（`list_platform_users()` 中不存在 `attacker`）；错误 `recent_password` 返回 `invalid_old` 且不落库；创建后经 `set_tenant_admin` 绑定已有账号可得到 1 个有效 `tenant_admin`。

## 4. 回归证据（失败集合一致性）

改动前（`git stash` 掉全部实现与测试改动，`HEAD` 状态）：

```
$ .venv/bin/python -m unittest discover -s tests -p "test_*.py"
Ran 891 tests ... FAILED (failures=18, errors=5)
```

改动后：

```
$ .venv/bin/python -m unittest discover -s tests -p "test_*.py"
Ran 902 tests ... FAILED (failures=18, errors=5)
```

两次 `FAIL`/`ERROR` 行排序后 diff **完全相同**（23 条，均为既有失败：SSRF、PDF 读取窗口、dashscope 常量、legacy todo、self-context 等，与租户创建无关）。用例数 891 → 902 即本次新增 11 个（7 服务层 + 4 handler）。

前端相关批次：

```
$ node --test tests/test_identity_admin_frontend.cjs tests/test_tenant_create_frontend.cjs \
              tests/test_nav_area_frontend.cjs tests/test_sidebar_account_frontend.cjs \
              tests/test_admin_home_frontend.cjs
ℹ tests 66   ℹ pass 66   ℹ fail 0
```

同一批次在改动前（仅 stash `identity-admin.js`）为 59/59 通过，故为纯增量、无回归。

注：把 `tests/*.cjs` 一次性喂给单个 `node --test` 进程会出现跨文件干扰导致的失败（`test_appearance_browser.cjs` 等），该现象在改动前同样存在且与本次改动无关；逐文件运行均通过。

## 5. 未覆盖 / 缺口

- 未做浏览器验收：模态框的实际视觉布局由既有 `test_identity_admin_frontend.cjs` 的说明界定为「浏览器验收负责」，本次未引入新的浏览器证据。
- 新租户在绑定首个管理员前无法自行创建账号（系统无平台建号入口），已记录于 `proposal.md` 的「已知范围限制」，需后续 change 处理。
